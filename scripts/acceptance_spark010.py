#!/usr/bin/env python3
"""Bounded fixed-input local/Standalone comparison, no learning-quality claim.

Execute once per Spark master in separate processes. Native cluster orchestration
and comparisons are in compare_spark010.py. The fixture is synthetic and frozen
in this script: it verifies identical computation, not real user usefulness.
"""
import argparse
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.learning.content import content_version
from knowpipe.recommendations.engine import recommend
from knowpipe.recommendations.index import build_index, load_index
from knowpipe.recommendations.runtime import create_spark, preflight, executor_statistics
from knowpipe.recommendations.text import ALGORITHM, document_key

FIXTURE = [
    ('official', 'redis-rdb', 'Redis persistence snapshots', 'Redis persistence saves data with RDB snapshots. RDB snapshots are periodic backups. Redis recovery loads the snapshot after a crash.'),
    ('qa', 'redis-aof', 'Redis persistence append log', 'Redis persistence uses AOF append only logging. AOF fsync policies choose durability and latency. Redis recovery can replay the operation log after a crash.'),
    ('tutorial', 'redis-recovery', 'Redis recovery practice', 'Redis persistence recovery restores a snapshot and replays the append log. Verify the backup with a restart exercise. Recovery testing finds storage errors before a production failure.'),
    ('official', 'postgres-isolation', 'PostgreSQL transaction isolation', 'PostgreSQL transaction isolation controls which changes concurrent transactions can see. Repeatable read uses a stable snapshot. Serializable transactions may need retry after a conflict.'),
    ('qa', 'postgres-rollback', 'Postgres transaction rollback', 'Postgres transaction rollback cancels changes after an error. Savepoints support partial rollback inside a transaction. Release database locks when the transaction finishes.'),
    ('tutorial', 'spring-transaction', 'Spring transaction callbacks', 'Spring transaction isolation callbacks manage application resources. Transaction rollback and retry happen around method invocation. This paragraph has generic vocabulary for an unrelated product.'),
    ('official', 'python-thread', 'Python thread synchronization', 'Python threads use a lock to coordinate shared state. Lock acquisition protects updates from concurrent access. Avoid deadlocks by acquiring resources in a fixed order.'),
    ('qa', 'docker-network', 'Docker container networking', 'Docker container networking connects services through a bridge network. A published port routes traffic from the host to a container. DNS resolves container service names.'),
    ('tutorial', 'kafka-offset', 'Kafka consumer offsets', 'Kafka consumer offsets track progress through topic partitions. A consumer commits offsets after processing messages. Replay starts from an earlier offset.'),
    ('official', 'filesystem', 'Linux file system writes', 'Linux file system writes enter a cache before durable storage. The fsync operation asks the kernel to synchronize file contents. Device failures still require backups.'),
    ('qa', 'garden', 'Growing flowers', 'A garden contains roses and soil. Water plants after dry weather. Compost adds nutrients to a flower bed.'),
    ('tutorial', 'redis-mirror', 'Redis persistence repeated source', 'Redis persistence saves data with RDB snapshots. RDB snapshots are periodic backups. Redis recovery loads the snapshot after a crash.'),
]


def fixture_snapshot(root):
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for source, doc_id, title, body in FIXTURE:
        rows.append({'source': source, 'doc_id': doc_id, 'title': title, 'body_text': body,
                     'language': 'en', 'source_url': 'https://example.invalid/' + doc_id,
                     'content_version': content_version({'body_text': body, 'language': 'en'}), 'doc_key': document_key(source, doc_id)})
    rows.sort(key=lambda r: (r['source'], r['doc_id']))
    encoded = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows)
    corpus_id = hashlib.sha256(ALGORITHM.encode() + encoded.encode()).hexdigest()
    directory = root / corpus_id
    directory.mkdir(exist_ok=True)
    path = directory / 'documents.jsonl'
    path.write_text(encoded, encoding='utf-8')
    return {'corpus_id': corpus_id, 'directory': str(directory), 'source_path': str(path),
            'document_count': len(rows), 'skipped_count': 0,
            'sources': {source: sum(r['source'] == source for r in rows) for source in sorted({r['source'] for r in rows})},
            'algorithm': ALGORITHM}, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    spark = index = None
    started = time.monotonic()
    result = {'status': 'failed', 'fixture': 'synthetic fixed 12 documents; engineering consistency only',
              'physical_computers_available': 1, 'multi_host_acceptance': 'pending_resources'}
    try:
        root = Path(args.index_root).resolve()
        snapshot, rows = fixture_snapshot(root)
        result['input_sha256'] = hashlib.sha256(Path(snapshot['source_path']).read_bytes()).hexdigest()
        spark = create_spark('knowpipe-feature010-deployment-acceptance', root)
        result['preflight'] = getattr(spark, '_knowpipe_preflight', None) or preflight(spark, root)
        begin = time.monotonic()
        build_index(spark, snapshot)
        result['index_seconds'] = round(time.monotonic() - begin, 3)
        result['snapshot'] = snapshot
        index = load_index(spark, snapshot)
        # The fixture is capped here; this collect is acceptance instrumentation,
        # not the production corpus pipeline.
        assert snapshot['document_count'] <= 100
        paragraph_rows = sorted([r.asDict(recursive=True) for r in index.paragraphs.collect()], key=lambda r: r['pid'])
        result['paragraphs_sha256'] = hashlib.sha256(json.dumps(paragraph_rows, sort_keys=True).encode()).hexdigest()
        result['terms'] = sorted([r.asDict() for r in index.terms.collect()], key=lambda r: r['term'])
        result['postings'] = sorted([r.asDict() for r in index.postings.collect()], key=lambda r: (r['pid'], r['term']))
        result['recommendations'] = []
        history_row = next(r for r in rows if r['doc_id'] == 'redis-rdb')
        history = [{k: history_row[k] for k in ('source', 'doc_id', 'content_version')}]
        for goal, known in [('Redis persistence recovery', []), ('Redis persistence recovery', history), ('PostgreSQL transaction rollback', [])]:
            begin = time.monotonic()
            observed = recommend(spark, index, goal, known)
            assert observed['items'], 'fixture_recommendations_empty'
            for item in observed['items']:
                row = next(r for r in rows if r['source'] == item['source'] and r['doc_id'] == item['doc_id'])
                evidence = item['goal_evidence']
                assert row['body_text'][evidence['start']:evidence['end']] == evidence['text']
            result['recommendations'].append({'goal': goal, 'history_count': len(known),
                'seconds': round(time.monotonic() - begin, 3), 'result': observed})
        result['runtime'] = {'master': spark.sparkContext.master, 'application_id': spark.sparkContext.applicationId,
                             'driver_host': socket.gethostname(), 'driver_pid': os.getpid(), **executor_statistics(spark)}
        result['status'] = 'passed'
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error)[:600])
        raise
    finally:
        result['seconds'] = round(time.monotonic() - started, 3)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if index:
            index.close()
        if spark:
            spark.stop()


if __name__ == '__main__':
    main()
