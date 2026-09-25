#!/usr/bin/env python3
"""Frozen bilingual development probes over the existing real fulltext Spark index.

Input interpretations must come from the actual configured translator. English
reference goals are separate probes, never replacements for a failed translation.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.recommendations.engine import recommend
from knowpipe.recommendations.index import load_index
from knowpipe.recommendations.query import original_query
from knowpipe.recommendations.runtime import create_spark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='evidence/010-quality-cluster-readiness')
    parser.add_argument('--interpretations', default='evidence/010-quality-cluster-readiness/goal-interpretations.json')
    parser.add_argument('--scale', default='evidence/009-fulltext-podcast-learning/scale.json')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    cases_file = output / 'goal-cases.json'
    cases = json.loads(cases_file.read_text())['cases']
    interpretations = json.loads(Path(args.interpretations).read_text())
    interpreted = {c['id']: c['query'] for c in interpretations['cases']}
    snapshot = json.loads(Path(args.scale).read_text())['index']
    directory = Path(snapshot['index_path']).parent
    snapshot.update(directory=str(directory), source_path=str(directory / 'documents.jsonl'))
    report_path = output / 'goal-retrieval.json'
    fingerprint = hashlib.sha256(cases_file.read_bytes() + Path(args.interpretations).read_bytes()).hexdigest()
    report = {'scope': 'fixed development probes, not independent relevance labels or learning benefit',
              'case_interpretation_sha256': fingerprint,
              'algorithm_files': {name: hashlib.sha256((Path(__file__).resolve().parents[1] /
                  'knowpipe' / 'recommendations' / name).read_bytes()).hexdigest()
                  for name in ('engine.py', 'query.py', 'text.py', 'index.py')},
              'corpus': {k: snapshot[k] for k in ('corpus_id', 'feature_id', 'document_count', 'paragraph_count', 'sources')},
              'cases': [], 'status': 'running'}
    if args.resume and report_path.exists():
        old = json.loads(report_path.read_text())
        if (old['case_interpretation_sha256'] != fingerprint or old['corpus'] != report['corpus'] or
                old.get('algorithm_files') != report['algorithm_files']):
            raise ValueError('resume_basis_changed')
        report['cases'] = old['cases']
    spark = index = None
    try:
        spark = create_spark('knowpipe-010-bilingual-acceptance', directory)
        report['master'] = spark.sparkContext.master
        index = load_index(spark, snapshot)
        for case in cases:
            for language in ('zh', 'en'):
                if any(r['id'] == case['id'] and r['language'] == language for r in report['cases']):
                    continue
                started = time.monotonic()
                query = interpreted[case['id']] if language == 'zh' else original_query(case['en'])
                assert query['original'] == case[language]
                result = recommend(spark, index, case[language], [], query_plan=query)
                # This verifies literal object constraints only, not teaching value.
                expected = {e['name'] for e in query['entities']}
                for item in result['items']:
                    assert {e['name'] for e in item['entity_evidence']} == expected
                row = {'id': case['id'], 'domain': case['domain'], 'language': language,
                       'seconds': round(time.monotonic() - started, 3), 'result': result}
                report['cases'].append(row)
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(case['id'], language, query['status'], len(result['items']), result['reason'], row['seconds'], flush=True)
        report['status'] = 'completed'
    except Exception as error:
        report['status'] = 'failed'; report['error_type'] = type(error).__name__
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if index: index.close()
        if spark: spark.stop()


if __name__ == '__main__':
    main()
