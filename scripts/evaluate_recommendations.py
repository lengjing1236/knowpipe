#!/usr/bin/env python3
"""Run fixed, external duplicate-query labels through actual Spark recommendations."""
import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from knowpipe.recommendations.engine import PARAMETERS, SELECTION_VERSION, recommend
from knowpipe.recommendations.evaluation import (
    COHORT_PREFIX, COHORT_SIZE, DATASET, fixed_queries, read_qrels,
    file_hash, retrieval_metrics, ungated_retrieval, verify_files,
)
from knowpipe.recommendations.index import build_index, load_index
from knowpipe.recommendations.text import ALGORITHM, document_key


def evaluation_snapshot(spark, data, root, manifest):
    """Keep external question bodies isolated from product fulltext declarations."""
    from pyspark.sql import functions as F
    corpus_id = hashlib.sha256((ALGORITHM + manifest['corpus.parquet']['sha256'] + ':evaluation-only').encode()).hexdigest()
    directory = root / corpus_id
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / 'documents.jsonl'
    corpus = spark.read.parquet(str(data / 'corpus.parquet'))
    count = corpus.count()
    if count != 32176 or corpus.select('_id').distinct().count() != count:
        raise ValueError('unexpected_corpus_count_or_duplicate_ids')
    if not raw.exists():
        source = 'beir-cqadupstack-programmers'
        key = F.udf(document_key, 'string')
        docs = corpus.select(F.col('_id').alias('doc_id'), 'title', F.col('text').alias('body_text')).withColumn(
            'source', F.lit(source)).withColumn('doc_key', key('source', 'doc_id')).withColumn('language', F.lit('en')).withColumn(
            'source_url', F.concat(F.lit('https://softwareengineering.stackexchange.com/questions/'), F.col('doc_id'))).withColumn(
            'content_version', F.sha2('body_text', 256))
        export = directory / 'export'
        docs.write.mode('overwrite').json(str(export))
        temporary = raw.with_suffix('.part')
        with temporary.open('wb') as stream:
            for part in sorted(export.glob('part-*.json')):
                with part.open('rb') as shard:
                    shutil.copyfileobj(shard, stream)
        temporary.replace(raw)
        shutil.rmtree(export)
    return {'corpus_id': corpus_id, 'directory': str(directory), 'source_path': str(raw),
            'document_count': count, 'skipped_count': 0, 'sources': {'beir-cqadupstack-programmers': count},
            'algorithm': ALGORITHM, 'evaluation_only': True, 'created_at': datetime.now(timezone.utc)}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description='固定外部标签评价；不代表个人学习收益')
    parser.add_argument('--data', default='state/feature009/evaluation')
    parser.add_argument('--index', default='state/feature009/evaluation-index')
    parser.add_argument('--output', default='evidence/009-fulltext-podcast-learning/retrieval-evaluation.json')
    parser.add_argument('--master', default='local[1]')
    parser.add_argument('--shuffle-partitions', type=int, default=16)
    parser.add_argument('--resume', action='store_true', help='校验固定输入及算法后复用已完成查询')
    args = parser.parse_args()
    if args.shuffle_partitions < 1:
        parser.error('--shuffle-partitions must be positive')
    data, root, output = Path(args.data).resolve(), Path(args.index).resolve(), Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = verify_files(data)
    source_root = Path(__file__).resolve().parents[1] / 'knowpipe' / 'recommendations'
    algorithm_files = {name: file_hash(source_root / name) for name in ('engine.py', 'text.py', 'query.py')}
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.master(args.master).appName('knowpipe-independent-retrieval-evaluation')
             .config('spark.sql.shuffle.partitions', str(args.shuffle_partitions))
             .config('spark.sql.ui.retainedExecutions', '2')
             .config('spark.ui.retainedJobs', '4').config('spark.ui.retainedStages', '8')
             .config('spark.sql.codegen.cache.maxEntries', '8').getOrCreate())
    spark.sparkContext.setLogLevel('ERROR')
    index = None
    started = time.monotonic()
    try:
        query_rows = spark.read.parquet(str(data / 'queries.parquet')).collect()  # fixed external set: 876 short queries
        queries = fixed_queries([row.asDict() for row in query_rows])
        query_ids = [str(query['_id']) for query in queries]
        qrels = read_qrels(data / 'qrels.tsv')
        selected_qrels = {qid: qrels[qid] for qid in query_ids}
        cohort = {'prefix': COHORT_PREFIX, 'count': COHORT_SIZE, 'query_ids': query_ids,
                  'selection': 'SHA256(prefix + query_id) ascending; fixed before retrieval', 'files': manifest}
        write_json(output.with_name('retrieval-cohort.json'), cohort)
        snapshot = evaluation_snapshot(spark, data, root, manifest)
        build_index(spark, snapshot)
        index = load_index(spark, snapshot)
        corpus_ids = index.documents.select('doc_id')
        if spark.createDataFrame([(qid,) for qid in query_ids], 'doc_id string').join(corpus_ids, 'doc_id').count():
            raise ValueError('query_in_corpus')
        unfiltered = ungated_retrieval(spark, index, queries)
        methods = {'tfidf_without_goal_gate': unfiltered, 'goal_gate_only': {}, 'goal_gate_mmr': {},
                   'without_history': {}, 'without_diversity': {}}
        results = []
        progress_path = output.with_name('retrieval-progress.json')
        if args.resume and progress_path.exists():
            progress = json.loads(progress_path.read_text(encoding='utf-8'))
            paused_path = output.with_name('retrieval-paused.json')
            if progress.get('algorithm_files') is not None:
                if progress.get('files') != manifest or progress['algorithm_files'] != algorithm_files:
                    raise ValueError('resume_frozen_files_mismatch')
            elif paused_path.exists():
                paused = json.loads(paused_path.read_text(encoding='utf-8'))
                if (paused.get('files') != manifest or paused.get('algorithm_files') != algorithm_files or
                        paused.get('progress_sha256') != file_hash(progress_path)):
                    raise ValueError('resume_frozen_files_or_progress_mismatch')
            else:
                raise ValueError('resume_requires_frozen_algorithm_files')
            if (progress.get('algorithm') != SELECTION_VERSION or progress.get('parameters') != PARAMETERS or
                    progress.get('corpus_id') != snapshot['corpus_id']):
                raise ValueError('resume_algorithm_or_corpus_mismatch')
            results = progress['queries']
            if [row['query_id'] for row in results] != query_ids[:len(results)]:
                raise ValueError('resume_cohort_mismatch')
            for row, query in zip(results, queries):
                qid = row['query_id']
                if row['query'] != query['text'] or row['positive_labels'] != selected_qrels[qid]:
                    raise ValueError('resume_query_or_labels_mismatch')
                for method in methods:
                    methods[method][qid] = row['rankings'][method]
            print(json.dumps({'resumed_queries': len(results), 'corpus_id': snapshot['corpus_id']}), flush=True)
        for query in queries[len(results):]:
            tick = time.monotonic()
            qid = str(query['_id'])
            result = recommend(spark, index, query['text'], [])
            key_to_id = {row.doc_key: row.doc_id for row in index.documents.filter(
                index.documents.doc_key.isin([r['doc_key'] for name in ('baseline', 'without_history', 'without_diversity')
                                               for r in result[name]])).select('doc_key', 'doc_id').collect()}
            methods['goal_gate_only'][qid] = [key_to_id[row['doc_key']] for row in result['baseline']]
            methods['goal_gate_mmr'][qid] = [item['doc_id'] for item in result['items']]
            for method in ('without_history', 'without_diversity'):
                methods[method][qid] = [key_to_id[row['doc_key']] for row in result[method]]
            results.append({'query_id': qid, 'query': query['text'], 'seconds': round(time.monotonic() - tick, 3),
                            'reason': result['reason'], 'rankings': {name: rows[qid] for name, rows in methods.items()},
                            'positive_labels': selected_qrels[qid]})
            write_json(progress_path, {'complete': False, 'queries': results,
                'algorithm': SELECTION_VERSION, 'parameters': PARAMETERS, 'corpus_id': snapshot['corpus_id'],
                'cohort': cohort, 'files': manifest, 'algorithm_files': algorithm_files})
            print(json.dumps({'finished': len(results), 'of': COHORT_SIZE, 'query_id': qid,
                              'returned': len(result['items']), 'seconds': results[-1]['seconds']}), flush=True)
        report = {'complete': True, 'dataset': DATASET, 'scope': '完整32,176候选问题，冻结32条外部测试查询；仅重复问题检索',
                  'cohort': cohort, 'corpus': snapshot, 'algorithm': SELECTION_VERSION, 'parameters': PARAMETERS,
                  'algorithm_files': algorithm_files,
                  'metrics': {name: retrieval_metrics(selected_qrels, rankings) for name, rankings in methods.items()},
                  'queries': results, 'elapsed_seconds': round(time.monotonic() - started, 3),
                  'elapsed_scope': '本次进程运行时长；各查询耗时包括恢复前已完成的查询，暂停等待时间不计入',
                  'query_seconds_total': round(sum(row['seconds'] for row in results), 3),
                  'spark_application_id': spark.sparkContext.applicationId, 'spark_master': spark.sparkContext.master,
                  'driver_memory': spark.sparkContext.getConf().get('spark.driver.memory'),
                  'shuffle_partitions': args.shuffle_partitions,
                  'evaluated_at': datetime.now(timezone.utc).isoformat(),
                  'limitations': [
                      '没有个人已读历史或补充价值标签；without_history 在此数据上与实际无历史推荐等价。',
                      'qrels 标记社区重复问题，不是教学适用性、答案正确性或个人学习收益。',
                      '未标注项按检索指标计零，不代表人工确认不相关；全部空结果仍计入宏平均。',
                      '查询和参数在观测结果前固定；仅32条查询，不能代表整个BEIR或广泛计算机技术质量。',
                      '只分析问题正文，与产品正文召回一致；不把标题并入正文，也不将评价问题冒充完整问答学习语料。',
                  ]}
        write_json(output, report)
        print(json.dumps({name: values['summary'] for name, values in report['metrics'].items()}, ensure_ascii=False, indent=2))
    finally:
        if index:
            index.close()
        spark.stop()


if __name__ == '__main__':
    main()
