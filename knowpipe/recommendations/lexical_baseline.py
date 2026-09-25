"""Spark lexical recall, history coverage and bounded evidence-based selection."""
from __future__ import annotations

from collections import Counter

from .text import document_key, tokenize
from .query import original_query, entity_pattern, entity_evidence

SELECTION_VERSION = 'goal-bilingual-object-coverage-mmr-v4'

PARAMETERS = {'candidates': 100, 'paragraphs_per_document': 3, 'top_k': 10,
              'min_relevance': .08, 'min_query_coverage': .5,
              'history_weight': .4, 'diversity_weight': .15, 'duplicate_threshold': .95,
              'supplement_min_overlap': .08, 'supplement_max_overlap': .7,
              'supplement_min_additional_terms': 2}

SUPPLEMENT_LIMITATION = ('仅比较当前目标下、版本一致的已读片段。列出的词未出现在这些对照片段中，'
                        '不表示用户不懂，也不证明语义上新增了知识。')


def _supplement(parts, history_by_pid, history_count, goal_term_count):
    """Present bounded, Spark-computed evidence; do not infer mastery or semantics."""
    chosen = max(parts, key=lambda p: (p.supplement_score, p.relevance, p.pid))
    prior = history_by_pid.get(chosen.right_pid)
    eligible = bool(chosen.supplement_eligible)
    status = 'lexical_candidate' if eligible else ('insufficient_history' if not history_count else 'insufficient_evidence')
    return {'status': status, 'method': 'goal-gated-lexical-comparison-v1',
            'candidate': {key: getattr(chosen, key) for key in ('pid', 'start', 'end', 'text', 'matched_terms')},
            'comparison': ({key: getattr(prior, key) for key in
                ('source', 'doc_id', 'title', 'content_version', 'pid', 'start', 'end', 'text')} if prior else None),
            'overlap': float(chosen.similarity),
            'goal_coverage': getattr(chosen, 'query_coverage', len(chosen.matched_terms) / goal_term_count),
            'shared_context_terms': chosen.shared_context_terms[:12],
            'additional_terms': chosen.additional_terms[:12] if history_count else [],
            'compared_history_paragraphs': history_count,
            'limitation': SUPPLEMENT_LIMITATION}, eligible


def _choose(candidates, pairs, use_history=True, use_diversity=True):
    """Executed as a Spark UDF on a bounded set, never on corpus-sized vectors."""
    similarity = {}
    for pair in pairs or []:
        similarity[(pair['left_key'], pair['right_key'])] = float(pair['similarity'])
        similarity[(pair['right_key'], pair['left_key'])] = float(pair['similarity'])
    remaining = {c['doc_key']: c for c in candidates or []}
    selected = []
    while remaining and len(selected) < PARAMETERS['top_k']:
        choices = []
        for key, item in remaining.items():
            overlap = float(item['history_overlap']) if use_history else 0.0
            if use_history and overlap >= .97:
                continue
            redundant = max((similarity.get((key, chosen[0]), 0.) for chosen in selected), default=0.) if use_diversity else 0.
            if redundant >= PARAMETERS['duplicate_threshold']:
                continue
            base = float(item['relevance']) * (1 - PARAMETERS['history_weight'] * overlap)
            score = (1 - PARAMETERS['diversity_weight']) * base - PARAMETERS['diversity_weight'] * redundant if use_diversity else base
            choices.append((score, key))
        if not choices:
            break
        score, key = sorted(choices, key=lambda row: (-row[0], row[1]))[0]
        if score <= 0:
            break
        selected.append((key, len(selected) + 1, score))
        del remaining[key]
    return selected


def _similarity(index, left_ids, right_ids):
    from pyspark.sql import functions as F
    left = index.postings.join(left_ids.select('pid').distinct(), 'pid').alias('l')
    right = index.postings.join(right_ids.select('pid').distinct(), 'pid').alias('r')
    return left.join(right, F.col('l.term') == F.col('r.term')).select(
        F.col('l.pid').alias('left_pid'), F.col('r.pid').alias('right_pid'),
        (F.col('l.weight') * F.col('r.weight')).alias('product')).groupBy('left_pid', 'right_pid').agg(
        F.least(F.lit(1.), F.sum('product')).alias('similarity'))


def recommend(spark, index, goal, history, query_plan=None):
    from pyspark.sql import functions as F, Window
    params = PARAMETERS
    result = {'items': [], 'baseline': [], 'without_history': [], 'without_diversity': [],
              'history_used': 0, 'history_unavailable': len(history), 'history_references': [],
              'algorithm': SELECTION_VERSION, 'parameters': dict(params), 'reason': None}
    if len(history) > 2000:
        raise ValueError('history_limit_exceeded')
    query_plan = query_plan or original_query(goal)
    result['query'] = query_plan
    branches = [Counter(tokenize(text)) for text in query_plan['variants'][:2]]
    terms = set().union(*(set(branch) for branch in branches))
    if any(len(branch) > 64 for branch in branches):
        raise ValueError('goal_term_limit_exceeded')
    if not index.snapshot['paragraph_count']:
        result['reason'] = 'no_fulltext'
        return result
    if not terms:
        result['reason'] = 'empty_goal_terms'
        return result
    query = spark.createDataFrame([(str(i), t, float(n), len(branch))
        for i, branch in enumerate(branches) for t, n in branch.items()],
        'variant string, term string, frequency double, term_count int').join(index.terms, 'term')
    query = query.withColumn('w', (1 + F.log('frequency')) * F.col('idf'))
    norms = query.groupBy('variant').agg(F.sqrt(F.sum(F.col('w') * F.col('w'))).alias('n'))
    if not norms.take(1):
        result['reason'] = 'no_matching_terms'
        return result
    query = query.join(norms, 'variant').filter(F.col('n') > 0).select(
        'term', 'variant', 'term_count', (F.col('w') / F.col('n')).alias('query_weight'))
    recall = index.postings.join(F.broadcast(query), 'term').groupBy('pid', 'variant', 'term_count').agg(
        F.least(F.lit(1.), F.sum(F.col('weight') * F.col('query_weight'))).alias('relevance'),
        F.sort_array(F.collect_set('term')).alias('matched_terms')).withColumn(
        'query_coverage', F.size('matched_terms') / F.col('term_count')).filter(
        (F.col('relevance') >= params['min_relevance']) &
        (F.col('query_coverage') >= params['min_query_coverage']))
    recall = recall.withColumn('_branch_rank', F.row_number().over(
        Window.partitionBy('pid').orderBy(F.desc('relevance'), F.desc('query_coverage'), 'variant'))).filter(
        F.col('_branch_rank') == 1).drop('_branch_rank', 'term_count').join(index.paragraphs, 'pid')
    entities = query_plan.get('entities', [])
    if entities:
        allowed = index.documents
        for entity in entities:
            pattern = entity_pattern(entity)
            allowed = allowed.filter(F.col('title').rlike(pattern) | F.col('body_text').rlike(pattern))
        recall = recall.join(allowed.select('doc_key'), 'doc_key', 'left_semi')
    recall = recall.cache()
    cached = [recall]
    try:
        refs = [(document_key(r['source'], r['doc_id']), r.get('content_version') or '') for r in history]
        history_frame = spark.createDataFrame(refs, 'doc_key string, read_version string')
        valid = history_frame.join(index.documents, 'doc_key').filter(F.col('read_version') == F.col('content_version')).dropDuplicates(['doc_key'])
        valid_rows = valid.select('source', 'doc_id', 'content_version', 'doc_key').collect()  # bounded by 2,000 explicit history entries
        result['history_references'] = [r.asDict() for r in valid_rows]
        result['history_used'] = len(valid_rows)
        result['history_unavailable'] = len(history) - len(valid_rows)
        read_keys = [r.doc_key for r in valid_rows]
        available = recall.filter(~F.col('doc_key').isin(read_keys)) if read_keys else recall
        candidates = available.groupBy('doc_key').agg(F.max('relevance').alias('relevance')).orderBy(F.desc('relevance'), 'doc_key').limit(params['candidates'])
        window = Window.partitionBy('doc_key').orderBy(F.desc('relevance'), F.asc('pid'))
        chosen_parts = available.join(candidates.select('doc_key'), 'doc_key').withColumn('position', F.row_number().over(window)).filter(
            F.col('position') <= params['paragraphs_per_document']).cache()
        cached.append(chosen_parts)
        if not chosen_parts.take(1):
            result['reason'] = 'no_object_candidates' if entities else 'no_relevant_candidates'
            return result
        history_parts = recall.filter(F.col('doc_key').isin(read_keys)) if read_keys else recall.filter(F.lit(False))
        history_count = history_parts.count()
        if history_count > 5000:
            raise ValueError('history_paragraph_limit_exceeded')
        result['history_relevant_paragraphs'] = history_count
        if history_count:
            overlap = _similarity(index, chosen_parts, history_parts)
            best = overlap.withColumn('n', F.row_number().over(Window.partitionBy('left_pid').orderBy(F.desc('similarity'), 'right_pid'))).filter(F.col('n') == 1)
            evidence = chosen_parts.join(best, chosen_parts.pid == best.left_pid, 'left').drop('left_pid', 'n').fillna({'similarity': 0.})
        else:
            evidence = chosen_parts.withColumn('right_pid', F.lit(None).cast('string')).withColumn('similarity', F.lit(0.))
        # Compare only goal-relevant history, but take the union of all its terms:
        # absence from the nearest paragraph alone is insufficient evidence.
        query_terms = F.array(*[F.lit(term) for term in sorted(terms)])
        if history_count:
            history_vocabulary = history_parts.select(F.explode('tokens').alias('term')).agg(
                F.collect_set('term').alias('history_terms'))
            prior_terms = history_parts.select(F.col('pid').alias('right_pid'), F.col('tokens').alias('prior_tokens'))
            evidence = evidence.join(prior_terms, 'right_pid', 'left').crossJoin(history_vocabulary)
        else:
            empty_terms = F.array().cast('array<string>')
            evidence = evidence.withColumn('history_terms', empty_terms).withColumn('prior_tokens', empty_terms)
        evidence = evidence.withColumn('additional_terms', F.sort_array(F.array_except(
            F.array_distinct('tokens'), F.array_union('history_terms', query_terms))))
        evidence = evidence.withColumn('shared_context_terms', F.sort_array(F.array_except(
            F.array_intersect('tokens', F.coalesce('prior_tokens', F.array().cast('array<string>'))), query_terms)))
        evidence = evidence.withColumn('supplement_eligible',
            F.lit(bool(history_count)) & F.col('right_pid').isNotNull() &
            (F.col('similarity') >= params['supplement_min_overlap']) &
            (F.col('similarity') <= params['supplement_max_overlap']) &
            (F.size('shared_context_terms') > 0) &
            (F.size('additional_terms') >= params['supplement_min_additional_terms']))
        evidence = evidence.withColumn('supplement_score', F.when(F.col('supplement_eligible'),
            F.col('relevance') * (1. - F.col('similarity'))).otherwise(0.)).drop('history_terms', 'prior_tokens')
        evidence = evidence.cache()
        cached.append(evidence)
        scores = evidence.groupBy('doc_key').agg(F.max('relevance').alias('relevance'),
            (F.sum(F.col('relevance') * F.col('similarity')) / F.sum('relevance')).alias('history_overlap')).cache()
        cached.append(scores)
        pair_scores = _similarity(index, chosen_parts, chosen_parts)
        mapping = chosen_parts.select('pid', 'doc_key')
        pairs = pair_scores.join(mapping.select(F.col('pid').alias('left_pid'), F.col('doc_key').alias('left_key')), 'left_pid').join(
            mapping.select(F.col('pid').alias('right_pid'), F.col('doc_key').alias('right_key')), 'right_pid').filter(
                F.col('left_key') < F.col('right_key')).groupBy('left_key', 'right_key').agg(F.max('similarity').alias('similarity'))
        compact = scores.agg(F.collect_list(F.struct('doc_key', 'relevance', 'history_overlap')).alias('candidates')).crossJoin(
            pairs.agg(F.collect_list(F.struct('left_key', 'right_key', 'similarity')).alias('pairs')))
        rank_type = 'array<struct<doc_key:string,rank:int,score:double>>'
        ranking = F.udf(lambda c, p: _choose(c, p), rank_type)
        no_history = F.udf(lambda c, p: _choose(c, p, False, True), rank_type)
        no_diversity = F.udf(lambda c, p: _choose(c, p, True, False), rank_type)
        ranked = compact.select(ranking('candidates', 'pairs').alias('selected'),
                                no_history('candidates', 'pairs').alias('without_history'),
                                no_diversity('candidates', 'pairs').alias('without_diversity')).first()
        result['baseline'] = [r.asDict() for r in scores.orderBy(F.desc('relevance'), 'doc_key').limit(params['top_k']).collect()]
        result['without_history'] = [r.asDict() for r in ranked.without_history]
        result['without_diversity'] = [r.asDict() for r in ranked.without_diversity]
        keys = [r.doc_key for r in ranked.selected]
        if not keys:
            result['reason'] = 'redundant_candidates'
            return result
        # Collect text only for selected documents (at most 30 bounded paragraphs).
        details = evidence.filter(F.col('doc_key').isin(keys)).orderBy('position', 'pid').collect()
        grouped = {}
        for row in details:
            grouped.setdefault(row.doc_key, []).append(row)
        history_ids = [row.right_pid for row in details if row.right_pid]
        hdocs = {r.pid: r for r in index.paragraphs.filter(F.col('pid').isin(history_ids)).collect()} if history_ids else {}
        score_map = {r.doc_key: r for r in scores.filter(F.col('doc_key').isin(keys)).collect()}
        entity_map = {}
        if entities:
            extract = F.udf(lambda title, body: entity_evidence(title, body, entities),
                            'array<struct<name:string,field:string,start:int,end:int,text:string>>')
            entity_map = {r.doc_key: [e.asDict() for e in r.evidence] for r in index.documents.filter(
                F.col('doc_key').isin(keys)).select('doc_key', extract('title', 'body_text').alias('evidence')).collect()}
        for rank in ranked.selected:
            row = grouped[rank.doc_key][0]
            match = max(grouped[rank.doc_key], key=lambda p: (p.similarity, p.pid))
            prior = hdocs.get(match.right_pid)
            item = {k: getattr(row, k) for k in ('source', 'doc_id', 'title', 'source_url', 'content_version', 'doc_key')}
            item.update(rank=rank.rank, selection_score=rank.score, relevance=score_map[rank.doc_key].relevance,
                        history_overlap=score_map[rank.doc_key].history_overlap,
                        reason='history_comparison' if history_count else 'goal_only',
                        goal_evidence={k: getattr(row, k) for k in ('start', 'end', 'text', 'matched_terms')},
                        history_evidence=None, entity_evidence=entity_map.get(rank.doc_key, []))
            if prior:
                item['history_evidence'] = {
                    'candidate': {k: getattr(match, k) for k in ('start', 'end', 'text')},
                    'history': {k: getattr(prior, k) for k in ('source', 'doc_id', 'title', 'content_version', 'start', 'end', 'text')},
                    'overlap': match.similarity}
            item['supplement_evidence'], item['supplement_eligible'] = _supplement(
                grouped[rank.doc_key], hdocs, history_count, len(terms))
            result['items'].append(item)
        return result
    finally:
        for frame in cached:
            frame.unpersist()
