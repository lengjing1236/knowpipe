"""Spark recall/coverage/selection with bounded, fallible semantic features."""
from __future__ import annotations

from collections import Counter
import re

from . import lexical_baseline
from .lexical_baseline import _supplement  # historical evidence-format compatibility
from .text import document_key, tokenize
from .query import original_query, document_matches_entity, entity_evidence
from .semantic_analysis import analyze, substantive, THRESHOLDS, LIMITATION

SELECTION_VERSION = 'goal-native-context-evidence-coverage-mmr-v6'
PARAMETERS = {'candidates': 100, 'paragraphs_per_document': 3, 'top_k': 3,
              'paragraphs_total': 300, 'paragraph_allocation': 'adaptive-seed-neighborhood-v1',
              'min_relevance': .03, 'min_query_coverage': .2,
              'history_weight': .6, 'supplement_weight': .4,
              'diversity_weight': .15, 'duplicate_threshold': .95,
              'semantic_thresholds': THRESHOLDS}


def _context_distance(start, end, anchors):
    """Character distance to any literal lexical anchor; overlapping windows unite."""
    return min((max(0, int(anchor['start']) - end, start - int(anchor['end']))
                for anchor in anchors), default=0)


def _expanded_parts(index, available, candidates):
    """Use the existing 300-paragraph budget inside already recalled documents.

    Nonmatching paragraphs can explain an API next to a matching anchor. This is
    still bounded candidate analysis, not a corpus-wide semantic embedding job.
    """
    from pyspark.sql import functions as F, Window
    count = candidates.count()
    if not count:
        return [], 0
    quota = max(PARAMETERS['paragraphs_per_document'], PARAMETERS['paragraphs_total'] // count)
    ranked = Window.partitionBy('doc_key').orderBy(F.desc('relevance'), 'pid')
    seeds = available.join(candidates.select('doc_key'), 'doc_key').withColumn(
        '_position', F.row_number().over(ranked)).filter(F.col('_position') <= PARAMETERS['paragraphs_per_document'])
    anchors = seeds.groupBy('doc_key').agg(F.collect_list(F.struct('start', 'end')).alias('_anchors'))
    seed_scores = seeds.select('pid', 'relevance', 'query_coverage', 'matched_terms').withColumn('_seed', F.lit(1))
    pool = index.paragraphs.join(candidates.select('doc_key'), 'doc_key').filter(
        F.udf(substantive, 'boolean')('text')).dropDuplicates(['pid']).join(anchors, 'doc_key').join(seed_scores, 'pid', 'left')
    pool = pool.fillna({'relevance': 0., 'query_coverage': 0., '_seed': 0}).withColumn(
        'matched_terms', F.coalesce('matched_terms', F.array().cast('array<string>')))
    distance = F.udf(_context_distance, 'long')
    pool = pool.withColumn('_distance', distance('start', 'end', '_anchors'))
    order = Window.partitionBy('doc_key').orderBy(F.desc('_seed'), '_distance', 'start', 'pid')
    selected = pool.withColumn('position', F.row_number().over(order)).filter(F.col('position') <= quota)
    return selected.drop('_anchors', '_distance').orderBy('doc_key', 'position').collect(), quota


def _choose(candidates, pairs, use_history=True, use_diversity=True):
    similarity = {}
    for pair in pairs or []:
        similarity[pair['left_key'], pair['right_key']] = float(pair['similarity'])
        similarity[pair['right_key'], pair['left_key']] = float(pair['similarity'])
    remaining = {row['doc_key']: row for row in candidates or []}
    selected = []
    while remaining and len(selected) < PARAMETERS['top_k']:
        choices = []
        for key, row in remaining.items():
            coverage = float(row['history_overlap']) if use_history else 0.
            supplement = float(row['supplement_score']) if use_history else 0.
            if coverage >= .999 and supplement == 0.:
                continue
            redundancy = max((similarity.get((key, prior[0]), 0.) for prior in selected), default=0.) if use_diversity else 0.
            if redundancy >= PARAMETERS['duplicate_threshold']:
                continue
            base = (float(row['relevance']) * (1. - PARAMETERS['history_weight'] * coverage)
                    + PARAMETERS['supplement_weight'] * supplement)
            score = ((1. - PARAMETERS['diversity_weight']) * base - PARAMETERS['diversity_weight'] * redundancy
                     if use_diversity else base)
            choices.append((score, key))
        if not choices:
            break
        score, key = sorted(choices, key=lambda row: (-row[0], row[1]))[0]
        if score <= 0.:
            break
        selected.append((key, len(selected) + 1, score))
        del remaining[key]
    return selected


def _degraded(spark, index, goal, history, query_plan, status, code, processor_id=None):
    result = lexical_baseline.recommend(spark, index, goal, history, query_plan=query_plan)
    result['algorithm'] = SELECTION_VERSION
    result['parameters'] = dict(PARAMETERS)
    result['semantic'] = {'status': status, 'error_code': code, 'processor_id': processor_id,
                          'comparison_scope': {'candidate_sentences': 0, 'history_sentences': 0}}
    result['items'] = result['items'][:PARAMETERS['top_k']]
    for item in result['items']:
        item['supplement_eligible'] = False
        item['supplement_evidence']['status'] = 'insufficient_evidence'
        item['supplement_evidence']['limitation'] = LIMITATION
        item['comparison'] = {'status': 'uncertain' if history else 'goal_only',
                              'candidate': item['goal_evidence'], 'history': None,
                              'covered_evidence': [], 'additional_evidence': [], 'uncertain_evidence': [],
                              'scores': {}, 'scope': {}, 'limitation': '语义比较不可用；这里只显示词汇目标匹配。'}
    return result


def recommend(spark, index, goal, history, query_plan=None, semantic=None):
    from pyspark.sql import functions as F, Window
    query_plan = query_plan or original_query(goal)
    if semantic is None:
        return _degraded(spark, index, goal, history, query_plan, 'unavailable', 'semantic_model_unavailable')
    processor_id = getattr(semantic, 'processor_id', None)
    if len(history) > 2000:
        raise ValueError('history_limit_exceeded')
    result = {'items': [], 'baseline': [], 'without_history': [], 'without_diversity': [],
              'history_used': 0, 'history_unavailable': len(history), 'history_references': [],
              'algorithm': SELECTION_VERSION, 'parameters': dict(PARAMETERS), 'reason': None,
              'query': query_plan, 'semantic': {'status': 'ready', 'processor_id': processor_id,
                  'comparison_scope': {}, 'error_code': None}}
    branches = [Counter(tokenize(text)) for text in query_plan['variants'][:2]]
    if any(len(branch) > 64 for branch in branches):
        raise ValueError('goal_term_limit_exceeded')
    if not index.snapshot['paragraph_count']:
        result['reason'] = 'no_fulltext'
        return result
    if not any(branches):
        result['reason'] = 'empty_goal_terms'
        return result
    query = spark.createDataFrame([(str(i), term, float(count), len(branch))
        for i, branch in enumerate(branches) for term, count in branch.items()],
        'variant string, term string, frequency double, term_count int').join(index.terms, 'term')
    query = query.withColumn('w', (1 + F.log('frequency')) * F.col('idf'))
    norms = query.groupBy('variant').agg(F.sqrt(F.sum(F.col('w') * F.col('w'))).alias('norm'))
    if not norms.take(1):
        result['reason'] = 'no_matching_terms'
        return result
    query = query.join(norms, 'variant').filter(F.col('norm') > 0).select(
        'term', 'variant', 'term_count', (F.col('w') / F.col('norm')).alias('query_weight'))
    native_chinese = bool(re.search(r'[\u4e00-\u9fff]', query_plan['variants'][0]))
    recall = index.postings.join(F.broadcast(query), 'term').groupBy('pid', 'variant', 'term_count').agg(
        F.least(F.lit(1.), F.sum(F.col('weight') * F.col('query_weight'))).alias('relevance'),
        F.sort_array(F.collect_set('term')).alias('matched_terms')).withColumn(
        'query_coverage', F.size('matched_terms') / F.col('term_count')).filter(
        (F.col('relevance') >= PARAMETERS['min_relevance']) &
        ((F.col('query_coverage') >= PARAMETERS['min_query_coverage']) |
         (F.lit(native_chinese) & (F.col('variant') == '0'))))
    recall = recall.withColumn('_branch', F.row_number().over(Window.partitionBy('pid').orderBy(
        F.desc('relevance'), F.desc('query_coverage'), 'variant'))).filter(F.col('_branch') == 1).drop('_branch').join(index.paragraphs, 'pid')
    # An isolated section heading cannot demonstrate a usable explanation.
    recall = recall.filter(F.udf(substantive, 'boolean')('text'))
    entities = query_plan.get('entities', [])
    if entities:
        allowed = index.documents
        for entity in entities:
            matches = F.udf(lambda title, body, source, url, e=entity:
                document_matches_entity(title, body, source, url, e), 'boolean')
            allowed = allowed.filter(matches('title', 'body_text', 'source', 'source_url'))
        recall = recall.join(allowed.select('doc_key'), 'doc_key', 'left_semi')
    recall = recall.cache()
    try:
        refs = [(document_key(row['source'], row['doc_id']), row.get('content_version') or '') for row in history]
        valid = spark.createDataFrame(refs, 'doc_key string, read_version string').join(index.documents, 'doc_key').filter(
            F.col('read_version') == F.col('content_version')).dropDuplicates(['doc_key'])
        valid_rows = valid.select('source', 'doc_id', 'content_version', 'doc_key').collect()
        result['history_references'] = [row.asDict() for row in valid_rows]
        result['history_used'] = len(valid_rows)
        result['history_unavailable'] = len(history) - len(valid_rows)
        read_keys = [row.doc_key for row in valid_rows]
        available = recall.filter(~F.col('doc_key').isin(read_keys)) if read_keys else recall
        candidates = available.groupBy('doc_key').agg(F.max('relevance').alias('relevance')).orderBy(
            F.desc('relevance'), 'doc_key').limit(PARAMETERS['candidates'])
        result['baseline'] = [row.asDict() for row in candidates.limit(PARAMETERS['top_k']).collect()]
        parts, paragraph_quota = _expanded_parts(index, available, candidates)
        if not parts:
            result['reason'] = 'no_object_candidates' if entities else 'no_relevant_candidates'
            return result
        history_parts = recall.filter(F.col('doc_key').isin(read_keys)) if read_keys else recall.filter(F.lit(False))
        result['history_relevant_paragraphs'] = history_parts.count()
        prior = [row.asDict() for row in history_parts.orderBy(F.desc('relevance'), 'doc_key', 'pid').limit(100).collect()]
        try:
            # Inputs and all collected text are bounded by 100 documents × 3
            # paragraphs and 100 history paragraphs; never corpus-sized vectors.
            features = analyze(semantic, goal, [row.asDict() for row in parts], prior)
        except Exception:
            return _degraded(spark, index, goal, history, query_plan, 'failed', 'semantic_processing_failed', processor_id)
        result['semantic'].update({key: features[key] for key in ('status', 'language', 'error_code', 'comparison_scope')})
        result['semantic']['comparison_scope'].update(native_original_wide_recall=native_chinese,
            recalled_documents=len({row.doc_key for row in parts}),
            paragraph_budget_per_document=paragraph_quota,
            nonlexical_context_paragraphs=sum(not row._seed for row in parts),
            history_paragraphs_available=result['history_relevant_paragraphs'],
            history_paragraphs_outside_budget=max(0, result['history_relevant_paragraphs'] - len(prior)))
        documents = features['documents']
        if not documents:
            result['reason'] = 'no_semantic_candidates'
            return result
        unit_rows = [(key, float(unit['relevance']), float(unit['covered']), float(unit['supplement']))
                     for key, doc in documents.items() for unit in doc['unit_features']]
        # Coverage and supplement are actual Spark inputs to the custom ranking;
        # they are not explanation-only annotations attached after selection.
        scores = spark.createDataFrame(unit_rows, 'doc_key string, relevance double, covered double, supplement double').groupBy('doc_key').agg(
            F.max('relevance').alias('relevance'), F.avg('covered').alias('history_overlap'),
            F.max('supplement').alias('supplement_score'))
        pairs = spark.createDataFrame([(p['left_key'], p['right_key'], p['similarity']) for p in features['pairs']],
                                      'left_key string, right_key string, similarity double')
        compact = scores.agg(F.collect_list(F.struct('doc_key', 'relevance', 'history_overlap', 'supplement_score')).alias('candidates')).crossJoin(
            pairs.agg(F.collect_list(F.struct('left_key', 'right_key', 'similarity')).alias('pairs')))
        rank_type = 'array<struct<doc_key:string,rank:int,score:double>>'
        selected = compact.select(
            F.udf(lambda c, p: _choose(c, p), rank_type)('candidates', 'pairs').alias('selected'),
            F.udf(lambda c, p: _choose(c, p, False, True), rank_type)('candidates', 'pairs').alias('without_history'),
            F.udf(lambda c, p: _choose(c, p, True, False), rank_type)('candidates', 'pairs').alias('without_diversity')).first()
        result['without_history'] = [row.asDict() for row in selected.without_history]
        result['without_diversity'] = [row.asDict() for row in selected.without_diversity]
        score_map = {row.doc_key: row.asDict() for row in scores.collect()}
        entity_map = {}
        if entities and selected.selected:
            selected_keys = [row.doc_key for row in selected.selected]
            extract = F.udf(lambda title, body, source, url:
                entity_evidence(title, body, entities, source=source, source_url=url),
                'array<struct<name:string,field:string,start:int,end:int,text:string>>')
            entity_map = {row.doc_key: [entry.asDict() for entry in row.evidence] for row in index.documents.filter(
                F.col('doc_key').isin(selected_keys)).select('doc_key', extract(
                    'title', 'body_text', 'source', 'source_url').alias('evidence')).collect()}
        for rank in selected.selected:
            doc = documents[rank.doc_key]
            row, comparison = doc['part'], doc['comparison']
            item = {key: row[key] for key in ('source', 'doc_id', 'title', 'source_url', 'content_version', 'doc_key')}
            item.update(rank=rank.rank, selection_score=rank.score, relevance=score_map[rank.doc_key]['relevance'],
                history_overlap=score_map[rank.doc_key]['history_overlap'],
                supplement_score=score_map[rank.doc_key]['supplement_score'],
                reason='history_comparison' if prior else 'goal_only', comparison=comparison,
                goal_evidence={**doc['goal_evidence'], 'matched_terms': sorted(
                    set(tokenize(doc['goal_evidence']['text'])).intersection(
                        set().union(*(set(branch) for branch in branches))))},
                history_evidence=({'candidate': comparison['candidate'], 'history': comparison['history'],
                                   'overlap': score_map[rank.doc_key]['history_overlap']} if comparison['history'] else None),
                entity_evidence=entity_map.get(rank.doc_key, []),
                supplement_eligible=comparison['status'] == 'possible_supplement',
                supplement_evidence={'status': comparison['status'], 'method': SELECTION_VERSION,
                    'candidate': comparison['candidate'], 'comparison': comparison['history'],
                    'additional_terms': [], 'shared_context_terms': [], 'limitation': LIMITATION,
                    'compared_history_paragraphs': len(prior)})
            result['items'].append(item)
        if not result['items']:
            result['reason'] = 'redundant_candidates'
        return result
    finally:
        recall.unpersist()
