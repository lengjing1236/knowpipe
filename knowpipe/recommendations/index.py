"""Streaming Mongo export and reusable Spark paragraph/posting snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..learning.content import content_view
from .text import ALGORITHM, document_key, paragraphs

DOCUMENT_SCHEMA = 'doc_key string, source string, doc_id string, title string, source_url string, content_version string, language string, body_text string'
MAX_DOCUMENTS = 50000
MAX_PARAGRAPHS = int(os.environ.get('LEARNING_MAX_PARAGRAPHS', '1000000'))


def prepare_snapshot(db, root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(ALGORITHM.encode())
    count = skipped = 0
    sources = {}
    handle, temp_path = tempfile.mkstemp(prefix='export-', suffix='.jsonl', dir=root)
    try:
        with os.fdopen(handle, 'w') as stream:
            for doc in db.documents.find({}, {'body_raw': 0}).sort([('source', 1), ('doc_id', 1)]):
                view = content_view(doc, include_text=False)
                if view['content_status'] != 'fulltext':
                    skipped += 1
                    continue
                count += 1
                if count > MAX_DOCUMENTS:
                    raise ValueError('document_limit_exceeded')
                record = {key: doc.get(key, '') for key in ('source', 'doc_id', 'title', 'source_url', 'language', 'body_text')}
                record.update(content_version=view['content_version'], doc_key=document_key(doc['source'], doc['doc_id']))
                encoded = json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n'
                digest.update(encoded.encode())
                stream.write(encoded)
                sources[doc['source']] = sources.get(doc['source'], 0) + 1
        corpus_id = digest.hexdigest()
        directory = root / corpus_id
        directory.mkdir(exist_ok=True)
        raw = directory / 'documents.jsonl'
        if raw.exists():
            os.unlink(temp_path)
        else:
            os.replace(temp_path, raw)
        return {'corpus_id': corpus_id, 'directory': str(directory), 'source_path': str(raw),
                'document_count': count, 'skipped_count': skipped, 'sources': sources,
                'algorithm': ALGORITHM, 'created_at': datetime.now(timezone.utc)}
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)



def _valid_artifacts(info):
    if not isinstance(info, dict) or not isinstance(info.get('index_path'), str) or not info['index_path']: return False
    root = Path(info.get('index_path', ''))
    return all((root / name).is_dir() and (root / name / '_SUCCESS').is_file() and any((root / name).glob('*.parquet'))
               for name in ('paragraphs', 'terms', 'postings'))



def _feature_id(snapshot, info):
    return hashlib.sha256(json.dumps([snapshot['algorithm'], snapshot['corpus_id'],
        info['idf_basis_corpus_id'], info.get('idf_policy'), info.get('previous_feature_id')],
        sort_keys=True).encode()).hexdigest()

def _partition_count(snapshot):
    # A JSONL file below Spark's default split size otherwise becomes one huge
    # Python/aggregate partition. Bound input work before exploding term arrays.
    return max(2, min(64, math.ceil(snapshot['document_count'] / 700)))

def build_index(spark, snapshot, previous=None):
    from pyspark.sql import functions as F
    root = Path(snapshot['directory'])
    meta = root / 'index.json'
    if meta.exists():
        try:
            cached = json.loads(meta.read_text())
        except (ValueError, OSError):
            cached = {}
        if _valid_artifacts(cached):
            snapshot.update(cached)
            return snapshot
        snapshot['rebuild_reason'] = 'cached_artifacts_unavailable'
    if previous and previous.get('corpus_id') != snapshot['corpus_id']:
        incremental = _build_incremental(spark, snapshot, previous)
        if incremental is not None:
            return incremental
    # Each attempt writes immutable files. A worker that loses its lease cannot
    # overwrite Parquet already being read by a replacement worker.
    output = Path(tempfile.mkdtemp(prefix='index-', dir=root))
    from pyspark import StorageLevel
    documents = spark.read.schema(DOCUMENT_SCHEMA).json(snapshot['source_path']).repartition(_partition_count(snapshot))
    splitter = F.udf(paragraphs, 'array<struct<pid:string,start:int,end:int,text:string,tokens:array<string>>>')
    parts = documents.withColumn('part', F.explode(splitter('doc_key', 'content_version', 'body_text'))).select(
        'doc_key', 'source', 'doc_id', 'title', 'source_url', 'content_version', 'language', 'part.*').persist(StorageLevel.DISK_ONLY)
    try:
        n = parts.count()
        if n > MAX_PARAGRAPHS:
            raise ValueError('paragraph_limit_exceeded')
        parts.write.mode('overwrite').parquet(str(output / 'paragraphs'))
        if n:
            counts = parts.select('pid', F.explode('tokens').alias('term')).groupBy('pid', 'term').count().persist(StorageLevel.DISK_ONLY)
            terms = counts.groupBy('term').agg(F.count('*').alias('df')).withColumn(
                'idf', F.log(F.lit(float(n + 1)) / (F.col('df') + 1)) + 1).select('term', 'idf')
            weighted = counts.join(terms, 'term').withColumn('w', (1 + F.log('count')) * F.col('idf'))
            norms = weighted.groupBy('pid').agg(F.sqrt(F.sum(F.col('w') * F.col('w'))).alias('norm'))
            postings = weighted.join(norms, 'pid').select('pid', 'term', (F.col('w') / F.col('norm')).alias('weight'))
        else:
            terms = spark.createDataFrame([], 'term string, idf double')
            postings = spark.createDataFrame([], 'pid string, term string, weight double')
        terms.write.mode('overwrite').parquet(str(output / 'terms'))
        postings.write.mode('overwrite').parquet(str(output / 'postings'))
        info = {'paragraph_count': n, 'spark_application_id': spark.sparkContext.applicationId,
                'spark_master': spark.sparkContext.master, 'index_path': str(output),
                'strategy': 'full', 'rebuild_reason': snapshot.get('rebuild_reason', 'initial'),
                'idf_basis_corpus_id': snapshot['corpus_id'], 'basis_document_count': snapshot['document_count'],
                'basis_paragraph_count': n, 'idf_policy': 'frozen_base_extend_zero_df',
                'incremental_generation': 0, 'cumulative_changed_documents': 0,
                'changed_documents': snapshot['document_count'], 'reused_documents': 0}
        info['feature_id'] = _feature_id(snapshot, info)
        marker = output / 'manifest.json'
        marker.write_text(json.dumps(info))
        os.replace(marker, meta)
        snapshot.update(info)
        return snapshot
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        parts.unpersist()
        if 'counts' in locals(): counts.unpersist()


def _build_incremental(spark, snapshot, previous):
    """Freeze IDF for a small delta; never mix vectors computed on different weights.

    The Mongo snapshot is still scanned and output Parquet is rewritten. Tokenizing
    and vectorizing unchanged documents are avoided, not all filesystem IO.
    """
    from pyspark.sql import functions as F
    if previous.get('algorithm') != snapshot['algorithm'] or not previous.get('idf_basis_corpus_id'):
        snapshot['rebuild_reason'] = 'incompatible_basis'
        return None
    if previous.get('incremental_generation', 0) >= 8:
        snapshot['rebuild_reason'] = 'periodic_rebuild'
        return None
    old_root = Path(previous.get('index_path', ''))
    if not _valid_artifacts(previous) or not Path(previous.get('source_path', '')).is_file():
        snapshot['rebuild_reason'] = 'basis_unavailable'
        return None
    current = spark.read.schema(DOCUMENT_SCHEMA).json(snapshot['source_path'])
    old = spark.read.schema(DOCUMENT_SCHEMA).json(previous['source_path'])
    identity = ['doc_key', 'content_version', 'title', 'source_url', 'language']
    unchanged = current.select(*identity).join(old.select(*identity), identity, 'inner').select('doc_key').cache()
    delta = current.join(unchanged, 'doc_key', 'left_anti')
    reused = unchanged.count()
    changed = snapshot['document_count'] - reused
    removed = previous['document_count'] - reused
    cumulative = previous.get('cumulative_changed_documents', 0) + max(changed, removed)
    basis_count = previous.get('basis_document_count', previous['document_count'])
    if max(changed, removed) > max(1, previous['document_count'] * .15) or cumulative > max(1, basis_count * .3):
        unchanged.unpersist()
        snapshot['rebuild_reason'] = 'change_fraction'
        return None
    splitter = F.udf(paragraphs, 'array<struct<pid:string,start:int,end:int,text:string,tokens:array<string>>>')
    new_parts = delta.withColumn('part', F.explode(splitter('doc_key', 'content_version', 'body_text'))).select(
        'doc_key', 'source', 'doc_id', 'title', 'source_url', 'content_version', 'language', 'part.*').cache()
    terms = spark.read.parquet(str(old_root / 'terms'))
    counts = new_parts.select('pid', F.explode('tokens').alias('term')).groupBy('pid', 'term').count().cache()
    output = None
    try:
        # Token occurrences, not just unique terms: a few identifiers must not force
        # a rebuild, while a new topic's dominant vocabulary should.
        token_total = counts.agg(F.sum('count')).first()[0] or 0
        missing = counts.join(terms, 'term', 'left_anti').agg(F.sum('count')).first()[0] or 0
        if token_total and missing / token_total > .25:
            snapshot['rebuild_reason'] = 'new_vocabulary'
            return None
        # New dimensions had df=0 in the frozen basis. Extend that vocabulary
        # without changing any existing dimension's weight or old vector norm.
        basis_paragraphs = previous.get('basis_paragraph_count', previous['paragraph_count'])
        added_terms = counts.select('term').distinct().join(terms.select('term'), 'term', 'left_anti').withColumn(
            'idf', F.lit(math.log(basis_paragraphs + 1.) + 1.))
        added_term_count = added_terms.count()
        terms = terms.unionByName(added_terms)
        retained = spark.read.parquet(str(old_root / 'paragraphs')).join(unchanged, 'doc_key', 'inner')
        combined = retained.unionByName(new_parts)
        n = combined.count()
        if n > MAX_PARAGRAPHS:
            raise ValueError('paragraph_limit_exceeded')
        output = Path(tempfile.mkdtemp(prefix='index-', dir=snapshot['directory']))
        combined.write.mode('overwrite').parquet(str(output / 'paragraphs'))
        weighted = counts.join(terms, 'term').withColumn('w', (1 + F.log('count')) * F.col('idf'))
        norms = weighted.groupBy('pid').agg(F.sqrt(F.sum(F.col('w') * F.col('w'))).alias('norm'))
        added = weighted.join(norms, 'pid').select('pid', 'term', (F.col('w') / F.col('norm')).alias('weight'))
        kept = spark.read.parquet(str(old_root / 'postings')).join(retained.select('pid'), 'pid', 'inner')
        kept.unionByName(added).write.mode('overwrite').parquet(str(output / 'postings'))
        terms.write.mode('overwrite').parquet(str(output / 'terms'))
        info = {'paragraph_count': n, 'spark_application_id': spark.sparkContext.applicationId,
                'spark_master': spark.sparkContext.master, 'index_path': str(output),
                'strategy': 'incremental', 'previous_corpus_id': previous['corpus_id'],
                'previous_feature_id': previous.get('feature_id'),
                'idf_basis_corpus_id': previous['idf_basis_corpus_id'], 'basis_document_count': basis_count,
                'basis_paragraph_count': basis_paragraphs, 'idf_policy': 'frozen_base_extend_zero_df',
                'added_terms_count': added_term_count,
                'incremental_generation': previous.get('incremental_generation', 0) + 1,
                'cumulative_changed_documents': cumulative, 'changed_documents': changed,
                'removed_or_changed_documents': removed, 'reused_documents': reused,
                'new_token_fraction': missing / token_total if token_total else 0}
        info['feature_id'] = _feature_id(snapshot, info)
        marker = output / 'manifest.json'
        marker.write_text(json.dumps(info))
        os.replace(marker, Path(snapshot['directory']) / 'index.json')
        snapshot.update(info)
        return snapshot
    except Exception:
        if output:
            shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        unchanged.unpersist()
        new_parts.unpersist()
        counts.unpersist()


class CorpusIndex:
    def __init__(self, spark, snapshot):
        self.snapshot = snapshot
        root = Path(snapshot.get('index_path', snapshot['directory']))
        from pyspark import StorageLevel
        self.paragraphs = spark.read.parquet(str(root / 'paragraphs')).persist(StorageLevel.MEMORY_AND_DISK)
        self.postings = spark.read.parquet(str(root / 'postings')).persist(StorageLevel.MEMORY_AND_DISK)
        self.terms = spark.read.parquet(str(root / 'terms')).cache()
        self.documents = spark.read.schema(DOCUMENT_SCHEMA).json(snapshot['source_path'])

    def close(self):
        for frame in (self.paragraphs, self.postings, self.terms):
            frame.unpersist()


def load_index(spark, snapshot):
    return CorpusIndex(spark, snapshot)


def mark_indexed(db, snapshot):
    from pymongo import UpdateOne
    pending = []
    with open(snapshot['source_path'], encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            pending.append(UpdateOne({'source': row['source'], 'doc_id': row['doc_id'],
                'content.kind': 'fulltext', 'content.version': row['content_version']}, {'$set': {
                    'recommendation_analysis': {'corpus_id': snapshot['corpus_id'], 'content_version': row['content_version']}}}))
            if len(pending) >= 500:
                db.documents.bulk_write(pending, ordered=False)
                pending = []
        if pending:
            db.documents.bulk_write(pending, ordered=False)
