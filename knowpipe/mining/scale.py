"""Freeze public input and measure real Mongo/Spark scale acceptance."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from .contract import validate_document, DocumentValidationError
from .dedup import exact_key, content_hash
from .batch import BatchStats, new_batch_id
from . import mongo_sink
from .spark_job import run_mining
from .collectors.stackexchange import collect


def clean_records(raw):
    rows, keys, hashes, ids = [], set(), set(), set()
    counts = dict(input=0, invalid=0, duplicate=0, valid=0)
    for item in raw:
        counts['input'] += 1
        try:
            row = validate_document(item)
        except (DocumentValidationError, TypeError):
            counts['invalid'] += 1
            continue
        key, digest, identity = exact_key(row), content_hash(row), (row['source'], row['doc_id'])
        if key in keys or digest in hashes or identity in ids:
            counts['duplicate'] += 1
            continue
        keys.add(key); hashes.add(digest); ids.add(identity)
        row.pop('_id', None)
        row['mining'] = None
        row['quality']['dedup_hash'] = digest
        rows.append(row)
    counts['valid'] = len(rows)
    return rows, counts


def verify_counts(db, batch_id, minimum=10000):
    current = {(r['source'], r['doc_id']) for r in db.documents.find(
        {'mining.batch_id': batch_id}, {'source':1, 'doc_id':1})}
    mined_rows = list(db.mining_results.find({'batch_id':batch_id}, {'source':1, 'doc_id':1}))
    mined = {(r['source'], r['doc_id']) for r in mined_rows}
    counts = dict(Counter(source for source, _ in current & mined))
    return {'sources': counts, 'matched_results': len(current & mined), 'result_rows':len(mined_rows), 'minimum': minimum,
            'accepted': counts.get('stackexchange', 0) >= minimum and counts.get('arxiv', 0) > 0
                        and current == mined and len(mined_rows) == len(mined)}


def process_tree_rss():
    """Linux RSS sample, summed across driver Python, Java and executor descendants."""
    processes = {}
    for p in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = p.read_text().rsplit(')', 1)[1].split()
            processes[int(p.parent.name)] = (int(fields[1]), int(fields[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError, IndexError):
            continue
    family = {os.getpid()}
    while True:
        grown = family | {pid for pid, (parent, _) in processes.items() if parent in family}
        if grown == family:
            return sum(processes.get(pid, (0, 0))[1] for pid in family)
        family = grown


def dump(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def freeze(db, snapshot, target):
    snapshot = Path(snapshot)
    if snapshot.exists():
        raise ValueError('snapshot_exists: choose a new path to preserve frozen input')
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    raw_path = snapshot.with_suffix('.raw.jsonl')
    raw = list(db.documents.find({}, {'_id':0}))
    error = None
    with raw_path.open('w') as out:
        for row in raw:
            out.write(json.dumps(row, default=str)+'\n')
        try:
            for row in collect(target):
                raw.append(row); out.write(json.dumps(row, default=str)+'\n'); out.flush()
        except Exception as exc:
            error = type(exc).__name__
    rows, counts = clean_records(raw)
    snapshot.write_text(''.join(json.dumps(r, default=str)+'\n' for r in rows))
    report = {**counts, 'sources':dict(Counter(r['source'] for r in rows)),
              'sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(), 'collection_error':error}
    dump(snapshot.with_suffix('.json'), report)
    if report['sources'].get('stackexchange', 0) < target:
        raise ValueError('insufficient_records: partial snapshot preserved')
    return report


def run(db, snapshot, output, minimum=10000):
    from pyspark.sql import SparkSession
    snapshot, output = Path(snapshot), Path(output)
    rows, quality = clean_records(json.loads(line) for line in snapshot.read_text().splitlines() if line.strip())
    if len({r['doc_id'] for r in rows}) != len(rows):
        raise ValueError('cross_source_doc_id_collision: existing mining pipeline requires globally unique doc_id')
    stats = BatchStats(new_batch_id(), sorted({r['source'] for r in rows}))
    stats.input_count = quality['input']; stats.skipped_count = quality['invalid']+quality['duplicate']
    event_dir = output.parent.resolve() / ('spark-events-'+stats.batch_id)
    event_dir.mkdir(parents=True, exist_ok=True)
    report = {'batch_id': stats.batch_id, 'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
              'quality':quality, 'parameters':{'top_k':10,'num_clusters':8,'top_similar':5,'shuffle_partitions':4},
              'logical_cpus':os.cpu_count(), 'code_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'event_log_dir':str(event_dir), 'status':'running', 'accepted':False}
    mongo_sink.ensure_indexes(db)
    mongo_sink.write_batch_stats(db, stats.to_dict())
    stop = threading.Event(); peak = [0]
    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], process_tree_rss()); stop.wait(.5)
    monitor = threading.Thread(target=sample, daemon=True); monitor.start()
    started = time.monotonic(); spark = None
    try:
        spark = (SparkSession.builder.master(os.environ.get('SPARK_MASTER','local[2]'))
                 .appName('knowpipe-scale').config('spark.sql.shuffle.partitions','4')
                 .config('spark.eventLog.enabled','true').config('spark.eventLog.dir',event_dir.as_uri()).getOrCreate())
        stats.spark_application_id = spark.sparkContext.applicationId
        stats.spark_master = spark.sparkContext.master
        results = run_mining(spark, rows, stats=stats)
        mongo_sink.upsert_documents(db, rows)
        mined = [{**results[r['doc_id']], 'source':r['source'], 'batch_id':stats.batch_id,
                  'reliable':not r['quality']['short_body']} for r in rows if r['doc_id'] in results]
        mongo_sink.write_mining_results(db, mined)
        stats.valid_count = len(mined); stats.finish()
        report.update(verify_counts(db, stats.batch_id, minimum), status='success')
    except Exception as exc:
        stats.finish(status='failed', error_message=type(exc).__name__)
        report.update(status='failed', error=type(exc).__name__)
        raise
    finally:
        if spark is not None:
            spark.stop()
        stop.set(); monitor.join(timeout=2)
        report.update(elapsed_seconds=round(time.monotonic()-started,3),
                      peak_process_tree_rss_bytes=peak[0], memory_measurement='0.5s Linux process-tree RSS samples; shared pages may be double-counted',
                      spark_application_id=getattr(stats,'spark_application_id',None), spark_master=getattr(stats,'spark_master',None))
        mongo_sink.write_batch_stats(db, {**stats.to_dict(), 'scale_evidence':report})
        dump(output, report)
    return report


def main():
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['collect','run'])
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27017')
    parser.add_argument('--mongo-db',required=True)
    parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--target',type=int,default=10500)
    parser.add_argument('--minimum',type=int,default=10000)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.target < 1 or args.minimum < 1 or (args.action=='run' and args.output is None):
        parser.error('positive counts and --output for run required')
    with MongoClient(args.mongo_uri,serverSelectionTimeoutMS=5000) as client:
        try:
            report=freeze(client[args.mongo_db],args.snapshot,args.target) if args.action=='collect' else run(client[args.mongo_db],args.snapshot,args.output,args.minimum)
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(report,default=str))
    if args.action=='run' and not report['accepted']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
