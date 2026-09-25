#!/usr/bin/env python3
"""Frozen real-source MVP cases; raw runs and evidence checks are separate.

Reference document hits alone are never product acceptance. Unjudged background
results remain pending source review. This suite was prepared by an agent, not
independent human learners. No paid API, download, or database mutation occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / 'evidence/011-mvp-recommendation-validation/cases.json'
STATE = ROOT / 'state/feature011/evaluation'
PACKAGE = 'knowpipe'


def module(name):
    return importlib.import_module(PACKAGE + '.' + name)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def load_frozen(path=DEFAULT_CASES):
    path = Path(path)
    expected = path.with_suffix('.sha256').read_text().split()[0]
    if digest(path) != expected:
        raise ValueError('frozen_case_manifest_changed')
    cases = read_json(path)
    source = ROOT / cases['background']['path']
    if digest(source) != cases['background']['sha256']:
        raise ValueError('frozen_background_changed')
    wanted = {d['doc_key']: d for d in cases['documents']}
    documents = {}
    for path in (source, STATE / 'controlled-documents.jsonl'):
        with path.open() as stream:
            for line in stream:
                doc = json.loads(line)
                key = doc['source'] + ':' + doc['doc_id']
                if key in wanted:
                    if hashlib.sha256(doc['body_text'].encode()).hexdigest() != wanted[key]['body_sha256']:
                        raise ValueError('reference_document_changed:' + key)
                    documents[key] = doc
    if set(documents) != set(wanted):
        raise ValueError('reference_document_missing')
    for fact in cases['facts']:
        if documents[fact['document']]['body_text'][fact['start']:fact['end']] != fact['text']:
            raise ValueError('reference_fact_offset_mismatch:' + fact['id'])
    return cases, documents


def verify_baseline():
    manifest = read_json(ROOT / 'evidence/011-mvp-recommendation-validation/baseline-code-manifest.json')
    package = ROOT / manifest['path']
    for relative, expected in manifest['files'].items():
        if digest(package / relative) != expected:
            raise ValueError('baseline_code_changed:' + relative)
    return package.parent


def selected_cases(manifest, split='all', case_ids=None, scope='all'):
    wanted = set(case_ids or [])
    rows = [case for case in manifest['cases'] if (split == 'all' or case['split'] == split)
            and (not wanted or case['id'] in wanted)
            and (scope == 'all' or (scope == 'background') == case['scope'].startswith('full_'))]
    if wanted - {case['id'] for case in rows}:
        raise ValueError('case_selection_missing_or_filtered')
    return rows


def source_code_hashes(package):
    return {str(p.relative_to(package)): digest(p) for p in package.rglob('*.py')}


def small_snapshot(spark, records):
    content_version = module('learning.content').content_version
    build_index = module('recommendations.index').build_index
    text = module('recommendations.text')
    ALGORITHM, document_key = text.ALGORITHM, text.document_key
    rows = []
    for doc in records:
        row = {k: doc.get(k, '') for k in ('source', 'doc_id', 'title', 'source_url', 'language', 'body_text')}
        row.update(content_version=content_version(doc), doc_key=document_key(doc['source'], doc['doc_id']))
        rows.append(row)
    body = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in sorted(rows, key=lambda r: r['doc_key']))
    corpus_id = hashlib.sha256((ALGORITHM + body).encode()).hexdigest()
    directory = STATE / 'controlled-index' / corpus_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'documents.jsonl').write_text(body)
    snapshot = {'corpus_id': corpus_id, 'directory': str(directory), 'source_path': str(directory / 'documents.jsonl'),
                'document_count': len(rows), 'skipped_count': 0, 'sources': dict(Counter(r['source'] for r in rows)), 'algorithm': ALGORITHM}
    return build_index(spark, snapshot)


def restricted_index(index, keys):
    """Declared controlled pool; keeps the frozen base IDF instead of retraining."""
    from pyspark.sql import functions as F
    parts = index.paragraphs.filter(F.col('doc_key').isin(keys))
    return SimpleNamespace(snapshot={**index.snapshot, 'controlled_candidate_pool': keys},
                           documents=index.documents.filter(F.col('doc_key').isin(keys)),
                           paragraphs=parts, terms=index.terms,
                           postings=index.postings.join(parts.select('pid'), 'pid'))


def run(args, manifest, documents):
    # Imports must happen after selecting the archived or current entire package.
    global PACKAGE
    package_parent = verify_baseline() if args.mode == 'baseline' else ROOT
    runtime_zip = None
    if args.mode == 'baseline':
        PACKAGE = 'knowpipe_baseline011'
        runtime_zip = STATE / 'baseline-v4' / 'knowpipe_baseline011.zip'
        with zipfile.ZipFile(runtime_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in (package_parent / 'knowpipe').rglob('*.py'):
                archive.write(file, PACKAGE + '/' + str(file.relative_to(package_parent / 'knowpipe')))
        sys.path.insert(0, str(runtime_zip))
    else:
        # A current run also freezes its whole imported package: another agent
        # editing production later cannot alter this run's Spark Python UDFs.
        PACKAGE = 'knowpipe_current011'
        source_hashes = source_code_hashes(ROOT / 'knowpipe')
        revision = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
        package_parent = STATE / 'current-snapshots' / revision
        for relative in source_hashes:
            target = package_parent / 'knowpipe' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(ROOT / 'knowpipe' / relative, target)
            if digest(target) != source_hashes[relative]:
                raise ValueError('current_code_snapshot_mismatch')
        runtime_zip = package_parent / 'knowpipe_current011.zip'
        with zipfile.ZipFile(runtime_zip, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in source_hashes:
                archive.write(package_parent / 'knowpipe' / relative, PACKAGE + '/' + relative)
        sys.path.insert(0, str(runtime_zip))
    content_version = module('learning.content').content_version
    configured_goal_translator = module('learning.local_providers').configured_goal_translator
    processor_identity = module('learning.providers').processor_identity
    recommend = module('recommendations.engine').recommend
    load_index = module('recommendations.index').load_index
    prepare_goal = module('recommendations.query').prepare_goal
    create_spark = module('recommendations.runtime').create_spark
    import mongomock
    selected = selected_cases(manifest, args.split, args.case, args.scope)
    db = mongomock.MongoClient().mvp011_ephemeral_query_cache
    translator = configured_goal_translator()
    semantic = None
    if args.mode == 'current' and not args.without_semantic:
        configured_semantic = module('recommendations.semantic').configured_semantic
        EnglishText = module('recommendations.semantic').EnglishText
        semantic = configured_semantic()
        if semantic is not None:
            semantic.text = EnglishText(translator, db.semantic_translations)
    signature = {'manifest_sha256': digest(args.cases), 'mode': args.mode,
                 'evaluator_sha256': digest(Path(__file__)),
                 'language': args.language, 'pool': args.pool, 'case_ids': [c['id'] for c in selected],
                 'code': source_code_hashes(package_parent / 'knowpipe'),
                 'goal_processor': processor_identity(translator),
                 'semantic_processor': processor_identity(semantic) if semantic is not None else None}
    report = {'status': 'running', 'scope': 'agent-prepared fixed source-based cases; not independent human learning outcome',
              'signature': signature, 'runtime_code_snapshot': str(package_parent), 'cases': [], 'started_at_unix': time.time()}
    if args.resume and Path(args.output).exists():
        old = read_json(args.output)
        if old.get('signature') != signature:
            raise ValueError('resume_basis_changed')
        report['cases'] = old['cases']
    write_json(args.output, report)
    spark = index = None
    loaded_kind = None
    try:
        spark = create_spark('knowpipe-011-mvp-' + args.mode, STATE)
        if runtime_zip is not None:
            spark.sparkContext.addPyFile(str(runtime_zip))
        report['spark_master'] = spark.sparkContext.master
        for case in selected:
            if any(r['id'] == case['id'] for r in report['cases']):
                continue
            kind = 'background' if case['scope'].startswith('full_') and args.pool == 'full' else 'controlled'
            if kind != loaded_kind:
                if index is not None:
                    index.close()
                if kind == 'background':
                    snapshot = read_json(ROOT / 'evidence/009-fulltext-podcast-learning/scale.json')['index']
                    directory = Path(snapshot['index_path']).parent
                    snapshot.update(directory=str(directory), source_path=str(directory / 'documents.jsonl'))
                else:
                    snapshot = small_snapshot(spark, documents.values())
                index = load_index(spark, snapshot)
                loaded_kind = kind
            goal = case['goal_' + args.language]
            history = [{'source': documents[k]['source'], 'doc_id': documents[k]['doc_id'],
                        'content_version': content_version(documents[k])} for k in case['history']]
            query = prepare_goal(db, goal, translator)
            pool_keys = [module('recommendations.text').document_key(documents[k]['source'], documents[k]['doc_id'])
                         for k in case.get('candidate_pool', [])]
            target = restricted_index(index, pool_keys) if pool_keys else index
            started = time.monotonic()
            kwargs = {'query_plan': query}
            if args.mode == 'current':
                kwargs['semantic'] = semantic
            result = recommend(spark, target, goal, history, **kwargs)
            row = {'id': case['id'], 'split': case['split'], 'scope': case['scope'], 'language': args.language,
                   'actual_pool': kind, 'product_background_requirement_met': kind == 'background',
                   'seconds': round(time.monotonic() - started, 3), 'history': history,
                   'candidate_pool': case.get('candidate_pool'),
                   'corpus': {k: index.snapshot.get(k) for k in ('corpus_id', 'feature_id', 'document_count', 'paragraph_count')},
                   'result': result}
            report['cases'].append(row)
            write_json(args.output, report)
            print(case['id'], len(result.get('items', [])), result.get('reason'), row['seconds'], flush=True)
        report['status'] = 'completed'
    except Exception as error:
        report['status'] = 'failed'
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['finished_at_unix'] = time.time()
        write_json(args.output, report)
        if index is not None:
            index.close()
        if spark is not None:
            spark.stop()
        if semantic is not None and hasattr(semantic, 'close'):
            semantic.close()


def key_for(item):
    # The frozen manifest uses human-readable source:ID references; production
    # doc_key is a SHA-256 identity. Preserve raw output and compare references.
    if item.get('source') and item.get('doc_id'):
        return str(item['source']) + ':' + str(item['doc_id'])
    return item.get('doc_key')


def valid_span(span, body):
    return (isinstance(span, dict) and isinstance(span.get('start'), int) and isinstance(span.get('end'), int)
            and 0 <= span['start'] < span['end'] <= len(body) and body[span['start']:span['end']] == span.get('text'))


def result_documents(manifest, raw):
    wanted = {key_for(i) for row in raw.get('cases', []) for i in row.get('result', {}).get('items', [])}
    wanted.update(key for case in manifest['cases'] for key in case['history'])
    documents = {}
    for path in (ROOT / manifest['background']['path'], STATE / 'controlled-documents.jsonl'):
        with path.open() as stream:
            for line in stream:
                doc = json.loads(line)
                if key_for(doc) in wanted:
                    documents[key_for(doc)] = doc
    return documents


def inspect_raw_evidence(raw, documents):
    """Audit every returned span before accepting a reviewer's own correct quote."""
    failures = []
    spans = 0

    def visit(value, path, default_key):
        nonlocal spans
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + '[' + str(index) + ']', default_key)
        elif isinstance(value, dict):
            key = key_for(value) or default_key
            doc = documents.get(key)
            if 'content_version' in value and doc:
                payload = json.dumps([doc.get('language') or 'und', doc['body_text']],
                                     ensure_ascii=False, separators=(',', ':'))
                if value['content_version'] != hashlib.sha256(payload.encode()).hexdigest():
                    failures.append({'path': path, 'doc_key': key, 'code': 'source_version_mismatch'})
            if {'start', 'end', 'text'}.issubset(value):
                spans += 1
                field = value.get('field', 'body_text')
                if field not in {'body_text', 'title', 'source_url'} or not doc or not valid_span(value, doc.get(field) or ''):
                    failures.append({'path': path, 'doc_key': key, 'code': 'returned_span_mismatch'})
            for name, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child, path + '.' + name, key)

    for row in raw.get('cases', []):
        for index, item in enumerate(row.get('result', {}).get('items', [])):
            key = key_for(item)
            if key not in documents:
                failures.append({'path': row['id'], 'doc_key': key, 'code': 'returned_document_missing'})
            if not item.get('goal_evidence'):
                failures.append({'path': row['id'], 'doc_key': key, 'code': 'goal_evidence_missing'})
            visit(item, row['id'] + '.items[' + str(index) + ']', key)
    return {'checked_spans': spans, 'invalid_count': len(failures), 'failures': failures,
            'status': 'valid_literal_evidence' if not failures else 'invalid_literal_evidence',
            'scope': 'literal source/version integrity only; does not certify relevance or supplement truth'}


def validate_reviews(manifest, raw, review_document, result_path):
    """Require review provenance and literal evidence, not an unbound yes/no file."""
    if review_document.get('result_sha256') != digest(result_path):
        raise ValueError('source_review_not_bound_to_results')
    if review_document.get('reviewer_type') not in {'agent_source_review', 'human_source_review'}:
        raise ValueError('source_review_provenance_required')
    bodies = {key: doc['body_text'] for key, doc in result_documents(manifest, raw).items()}
    cases = {c['id']: c for c in manifest['cases']}
    returned = {(r['id'], key_for(i)) for r in raw.get('cases', []) for i in r.get('result', {}).get('items', [])}
    seen = set()
    for review in review_document.get('judgments', []):
        identity = (review['case_id'], review['doc_key'])
        if identity not in returned or identity in seen or not review.get('reason_zh'):
            raise ValueError('invalid_or_duplicate_source_review')
        seen.add(identity)
        evidence = review.get('evidence', [])
        if not evidence or any(not valid_span(e, bodies.get(e.get('doc_key'), '')) for e in evidence):
            raise ValueError('source_review_evidence_mismatch')
        keys = {e['doc_key'] for e in evidence}
        if review['doc_key'] not in keys:
            raise ValueError('candidate_evidence_required')
        if review.get('supplement') == 'supported' and not keys.intersection(cases[review['case_id']]['history']):
            raise ValueError('supported_supplement_requires_history_evidence')
        raw_item = next(i for row in raw['cases'] if row['id'] == review['case_id']
                        for i in row['result'].get('items', []) if key_for(i) == review['doc_key'])
        claims = (raw_item.get('comparison') or {}).get('additional_evidence', [])
        seen_claims = set()
        for judgment in review.get('claims', []):
            position = judgment.get('claim_index')
            if not isinstance(position, int) or position < 0 or position >= len(claims) or position in seen_claims:
                raise ValueError('invalid_claim_review_index')
            seen_claims.add(position)
            if judgment.get('judgment') not in {'supported', 'unsupported', 'uncertain'} or not judgment.get('reason_zh'):
                raise ValueError('claim_source_review_required')
            spans = judgment.get('evidence', [])
            if not spans or any(not valid_span(e, bodies.get(e.get('doc_key'), '')) for e in spans):
                raise ValueError('claim_source_evidence_mismatch')
            if not {e['doc_key'] for e in spans}.intersection(cases[review['case_id']]['history']):
                raise ValueError('claim_history_evidence_required')
    return review_document['judgments']


def score_results(manifest, raw, reviews=None):
    """Only explicit frozen negatives and reviewed assertions produce judgments.

    A reference ID hit is diagnostic. It does not prove its returned passage
    teaches the target or that a supplement claim is true.
    """
    review_map = {(r['case_id'], r['doc_key']): r for r in reviews or []}
    cases = {c['id']: c for c in manifest['cases']}
    scored = []
    counts = Counter()
    for row in raw.get('cases', []):
        case = cases[row['id']]
        items = row.get('result', {}).get('items', [])
        judgments = []
        for item in items:
            key = key_for(item)
            review = review_map.get((case['id'], key), {})
            relevance = review.get('relevance', 'unjudged')
            if key in case.get('explicit_distractors', []):
                relevance = 'irrelevant'
            supplement = review.get('supplement', 'unjudged')
            eligible = bool(item.get('supplement_eligible'))
            if eligible and (case['expected'].get('must_not_claim_supplement') or
                             key in case.get('explicit_distractors', []) or key in case['history']):
                supplement = 'unsupported'
            if relevance not in {'direct', 'partial', 'irrelevant', 'unjudged'}:
                raise ValueError('invalid_review_relevance')
            if supplement not in {'supported', 'unsupported', 'uncertain', 'unjudged'}:
                raise ValueError('invalid_review_supplement')
            judgments.append({'doc_key': key, 'rank': item.get('rank'), 'relevance': relevance,
                              'real_source': not str(key).startswith('mvp_fixture:'),
                              'reference_document_hit': key in case.get('reference_relevant', []),
                              'supplement_eligible': eligible, 'supplement': supplement,
                              'claims': review.get('claims', []),
                              'claim_reviews_pending': max(0, len((item.get('comparison') or {}).get('additional_evidence', []))
                                                           - len(review.get('claims', []))) if eligible else 0,
                              'review_reason_zh': review.get('reason_zh')})
        top = judgments[:3]
        known = [j for j in top if j['relevance'] != 'unjudged']
        counts['top3_total'] += len(top)
        counts['top3_judged'] += len(known)
        counts['top3_direct'] += sum(j['relevance'] == 'direct' for j in known)
        counts['false_supplement'] += sum(max(
            sum(c.get('judgment') == 'unsupported' for c in j['claims']),
            int(j['supplement_eligible'] and j['supplement'] == 'unsupported')) for j in judgments)
        counts['supplement_claims_pending'] += sum(j['claim_reviews_pending'] for j in judgments)
        counts['supplement_claims_uncertain'] += sum(c.get('judgment') == 'uncertain'
                                                   for j in judgments for c in j['claims'])
        required = case['expected'].get('top3_reference_or_fact_equivalent_required', False)
        real_source_required = case.get('scope', '').startswith('full_')
        direct = any(j['relevance'] == 'direct' and (j['real_source'] or not real_source_required) for j in top)
        semantic = row.get('result', {}).get('semantic')
        computation_complete = (not row.get('result', {}).get('error_code') and
                                (semantic is None or (semantic.get('status') == 'ready' and not semantic.get('error_code'))))
        counts['incomplete_computation'] += int(not computation_complete)
        result = {'id': case['id'], 'judgments': judgments, 'items': len(items),
                  'computation_complete': computation_complete, 'real_source_required': real_source_required,
                  'reference_top3_hit': any(j['reference_document_hit'] for j in top),
                  'direct_top3_required': required, 'direct_top3_confirmed': direct,
                  'source_review_pending': sum(j['relevance'] == 'unjudged' for j in top),
                  'positive_supplement_required': case['expected'].get('personal_supplement_supported', False),
                  'positive_supplement_confirmed': any(j['supplement_eligible'] and j['supplement'] == 'supported'
                                                      and (j['real_source'] or not real_source_required) for j in judgments),
                  'empty_required': case['expected'].get('empty_required', False),
                  'empty_check': not items and computation_complete if case['expected'].get('empty_required') else None,
                  'history_exclusion_violation': any(key_for(i) in case['history'] for i in items)}
        scored.append(result)
    counts['top3_unjudged'] = counts['top3_total'] - counts['top3_judged']
    return {'status': 'evidence_incomplete' if counts['top3_unjudged'] or counts['supplement_claims_pending'] or
            counts['supplement_claims_uncertain'] or counts['incomplete_computation'] else 'source_checks_complete_not_full_mvp_verdict',
            'scope': 'source judgments and deterministic counterexamples; Web, language, and ablations require separate evidence',
            'review_provenance': 'agent source review, not independent human labels', 'counts': dict(counts),
            'judged_top3_direct_rate': counts['top3_direct'] / counts['top3_judged'] if counts['top3_judged'] else None,
            'missing_case_ids': sorted(set(cases) - {r['id'] for r in raw.get('cases', [])}), 'cases': scored}


def review_template(manifest, raw, result_path):
    """Prepare real returned passages for an explicit source review, not labels."""
    rows = []
    for result in raw.get('cases', []):
        for item in result.get('result', {}).get('items', [])[:3]:
            key = key_for(item)
            evidence = []
            goal = item.get('goal_evidence')
            if goal:
                evidence.append({'doc_key': key, **{k: goal[k] for k in ('start', 'end', 'text')}})
            prior = (item.get('history_evidence') or {}).get('history')
            if prior:
                evidence.append({'doc_key': key_for(prior), **{k: prior[k] for k in ('start', 'end', 'text')}})
            rows.append({'case_id': result['id'], 'doc_key': key, 'title': item.get('title'),
                         'source_url': item.get('source_url'), 'rank': item.get('rank'),
                         'relevance': 'unjudged', 'supplement': 'unjudged', 'reason_zh': '',
                         'evidence': evidence,
                         'claims': [{'claim_index': position, 'judgment': 'unjudged', 'reason_zh': '',
                                     'evidence': [{'doc_key': key_for(span), **{k: span[k] for k in ('start', 'end', 'text')}}
                                                  for span in (claim.get('candidate'), claim.get('history')) if span]}
                                    for position, claim in enumerate((item.get('comparison') or {}).get('additional_evidence', []))],
                         'reference_fact_ids': [f['id'] for f in manifest['facts'] if f['document'] == key]})
    return {'result_sha256': digest(result_path), 'reviewer_type': 'agent_source_review',
            'status': 'template_not_reviewed', 'judgments': rows,
            'case_failure_review': [{'case_id': r['id'], 'failure_class': None, 'reason_zh': '',
                                     'query': r.get('result', {}).get('query')} for r in raw.get('cases', [])],
            'allowed_failure_classes': ['none', 'corpus', 'goal_translation', 'recall', 'ranking', 'duplicate',
                                        'supplement_assertion', 'evidence', 'chinese_reading', 'flow', 'insufficient_evidence']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'run', 'score', 'review-template'])
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--mode', choices=['baseline', 'current'], default='baseline')
    parser.add_argument('--scope', choices=['all', 'background', 'controlled'], default='all')
    parser.add_argument('--pool', choices=['full', 'small'], default='full',
                        help='small is mechanism debugging only; full keeps 10,215 background for core cases')
    parser.add_argument('--split', choices=['all', 'development', 'reserved_validation'], default='development')
    parser.add_argument('--case', action='append')
    parser.add_argument('--language', choices=['zh', 'en'], default='zh')
    parser.add_argument('--output', default=str(ROOT / 'evidence/011-mvp-recommendation-validation/evaluation-raw.json'))
    parser.add_argument('--results')
    parser.add_argument('--reviews')
    parser.add_argument('--without-semantic', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    manifest, documents = load_frozen(args.cases)
    if args.command == 'verify':
        verify_baseline()
        print(json.dumps({'status': 'verified', 'manifest_sha256': digest(args.cases), 'cases': len(manifest['cases']),
                          'documents': len(documents), 'facts': len(manifest['facts'])}))
    elif args.command == 'run':
        run(args, manifest, documents)
    else:
        if not args.results:
            parser.error('--results is required for score')
        raw = read_json(args.results)
        if raw.get('signature', {}).get('manifest_sha256') != digest(args.cases):
            raise ValueError('results_not_bound_to_frozen_cases')
        if args.command == 'review-template':
            write_json(args.output, review_template(manifest, raw, args.results))
        else:
            reviews = validate_reviews(manifest, raw, read_json(args.reviews), args.results) if args.reviews else []
            report = score_results(manifest, raw, reviews)
            report['raw_evidence_integrity'] = inspect_raw_evidence(raw, result_documents(manifest, raw))
            report['evaluator_sha256'] = digest(Path(__file__))
            if report['raw_evidence_integrity']['invalid_count']:
                report['status'] = 'invalid_returned_evidence'
            write_json(args.output, report)


if __name__ == '__main__':
    main()
