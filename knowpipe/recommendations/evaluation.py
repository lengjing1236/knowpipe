"""External retrieval evaluation, distinct from learner benefit or personalization."""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path


DATASET = 'BeIR/cqadupstack/programmers'
COHORT_PREFIX = 'knowpipe-009-holdout:'
COHORT_SIZE = 32
FILES = {
    'corpus.parquet': ('BeIR/cqadupstack', 'e84b7220ce5a4e6c49aeb568983b0ec5a7584c2c',
                       'programmers/corpus/corpus-00000-of-00001.parquet',
                       'b8eefa212252fe5af6ee72ba530df053740ff30c2c0bea77089f8a9271e94f75'),
    'queries.parquet': ('BeIR/cqadupstack', 'e84b7220ce5a4e6c49aeb568983b0ec5a7584c2c',
                        'programmers/queries/queries-00000-of-00001.parquet',
                        '106ba4777034ccec608a623ac93b5cfad63d89effc64d0528759ea94e276345d'),
    'qrels.tsv': ('BeIR/cqadupstack-qrels', '13595a9f221256a9a21e91c8b0fed3563382b6a7', 'programmers/test.tsv',
                  'ed69afa873c64d34298792d694c82c4293e4b7e3c6ea3edc7a73662d4b0cba76'),
}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(directory):
    result = {}
    for name, (repo, revision, relative, expected) in FILES.items():
        path = Path(directory) / name
        actual = file_hash(path)
        if actual != expected:
            raise ValueError(f'evaluation_file_hash_mismatch:{name}')
        result[name] = {'sha256': actual, 'bytes': path.stat().st_size,
                        'origin': f'https://huggingface.co/datasets/{repo}/resolve/{revision}/{relative}'}
    return result


def fixed_queries(rows):
    """IDs alone determine inclusion, before any relevance scores are observed."""
    return sorted(rows, key=lambda row: hashlib.sha256(
        (COHORT_PREFIX + str(row['_id'])).encode()).hexdigest())[:COHORT_SIZE]


def read_qrels(path):
    result = {}
    with Path(path).open(encoding='utf-8') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            result.setdefault(row['query-id'], {})[row['corpus-id']] = int(row['score'])
    return result


def retrieval_metrics(qrels, rankings, k=10):
    """Macro-average all queries, including empty rankings; binary qrels only.

    CQADupStack's label 1 is a community duplicate link. Unjudged candidates
    receive 0 by the retrieval convention, not an assertion of irrelevance.
    """
    if k <= 0 or not qrels:
        raise ValueError('positive_k_and_nonempty_qrels_required')
    per_query = {}
    for query_id, labels in qrels.items():
        if any(value not in (0, 1) for value in labels.values()):
            raise ValueError('binary_qrels_required')
        relevant = {doc_id for doc_id, score in labels.items() if score > 0}
        if not relevant:
            raise ValueError('each_query_requires_positive_label')
        # The query itself is never a retrieved answer. Duplicate output IDs
        # cannot inflate metrics or consume additional relevance credit.
        ranking = list(dict.fromkeys(str(i) for i in rankings.get(query_id, []) if str(i) != query_id))[:k]
        hits = [rank + 1 for rank, doc_id in enumerate(ranking) if doc_id in relevant]
        ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
        per_query[query_id] = {
            f'nDCG@{k}': sum(1 / math.log2(rank + 1) for rank in hits) / ideal,
            f'Recall@{k}': len(hits) / len(relevant),
            f'MRR@{k}': 1 / hits[0] if hits else 0.,
            f'Precision@{k}': len(hits) / k,
            'empty': float(not ranking), 'returned': len(ranking),
        }
    keys = (f'nDCG@{k}', f'Recall@{k}', f'MRR@{k}', f'Precision@{k}', 'empty', 'returned')
    summary = {key: sum(row[key] for row in per_query.values()) / len(per_query) for key in keys}
    summary['queries'] = len(per_query)
    return {'summary': summary, 'per_query': per_query}


def ungated_retrieval(spark, index, queries, k=10):
    """Same Spark paragraph TF-IDF, without production relevance/coverage gates."""
    from collections import Counter
    from pyspark.sql import functions as F, Window
    from .text import tokenize

    rows = [(str(q['_id']), term, float(count)) for q in queries for term, count in Counter(tokenize(q['text'])).items()]
    query = spark.createDataFrame(rows, 'query_id string, term string, frequency double').join(index.terms, 'term')
    query = query.withColumn('w', (1 + F.log('frequency')) * F.col('idf'))
    norms = query.groupBy('query_id').agg(F.sqrt(F.sum(F.col('w') ** 2)).alias('norm'))
    query = query.join(norms, 'query_id').select('query_id', 'term', (F.col('w') / F.col('norm')).alias('query_weight'))
    hits = index.postings.join(F.broadcast(query), 'term').groupBy('query_id', 'pid').agg(
        F.sum(F.col('weight') * F.col('query_weight')).alias('score')).join(index.paragraphs.select('pid', 'doc_id'), 'pid')
    hits = hits.filter(F.col('query_id') != F.col('doc_id')).groupBy('query_id', 'doc_id').agg(F.max('score').alias('score'))
    window = Window.partitionBy('query_id').orderBy(F.desc('score'), 'doc_id')
    result = {str(q['_id']): [] for q in queries}
    for row in hits.withColumn('rank', F.row_number().over(window)).filter(F.col('rank') <= k).orderBy('query_id', 'rank').collect():
        result[row.query_id].append(row.doc_id)
    return result
