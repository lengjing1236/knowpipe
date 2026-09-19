"""Versioned podcast-to-document lexical links, computed in a shared Spark space."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from ..podcasts.analysis import tokens

ALGORITHM = 'shared-tfidf-cosine-v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def document_fingerprint(doc):
    return fingerprint([doc['source'],doc['doc_id'],doc.get('title',''),doc.get('body_text','')])


def episode_fingerprint(episode):
    return fingerprint([episode.get('batch_id'),episode.get('analysis',{}).get('segments',[])])


def rank_links(spark, documents, episodes, top_k=3):
    from pyspark.ml.feature import CountVectorizer, IDF, Normalizer, StopWordsRemover
    from pyspark.sql import functions as F, types as T, Window
    rows=[]
    for d in documents:
        rows.append(('document',d['source'],d['doc_id'],'',-1,tokens(d.get('title','')+' '+d.get('body_text',''))))
    for e in episodes:
        for s in e['analysis']['segments']:
            rows.append(('segment','','',e['episode_id'],int(s['segment_id']),tokens(s['text'])))
    frame=spark.createDataFrame(rows,'kind string, source string, doc_id string, episode_id string, segment_id int, raw_tokens array<string>')
    frame=StopWordsRemover(inputCol='raw_tokens',outputCol='tokens').transform(frame).filter(F.size('tokens')>0)
    if frame.select('kind').distinct().count()<2:
        return
    cv=CountVectorizer(inputCol='tokens',outputCol='tf',vocabSize=20000,maxDF=.95).fit(frame)
    vocabulary = cv.vocabulary
    if not vocabulary:
        return
    tf=cv.transform(frame)
    weighted=IDF(inputCol='tf',outputCol='features').fit(tf).transform(tf)
    normalized=Normalizer(inputCol='features',outputCol='norm').transform(weighted)
    entry_type=T.ArrayType(T.StructType([T.StructField('term',T.IntegerType()),T.StructField('weight',T.DoubleType())]))
    entries=F.udf(lambda v:[(int(i),float(w)) for i,w in zip(v.indices,v.values) if w>0],entry_type)
    inverted=normalized.select('kind','source','doc_id','episode_id','segment_id',F.explode(entries('norm')).alias('entry')).cache()
    try:
        left=inverted.filter(F.col('kind')=='segment').alias('s')
        right=inverted.filter(F.col('kind')=='document').alias('d')
        products=left.join(right,F.col('s.entry.term')==F.col('d.entry.term')).select(
            F.col('s.episode_id').alias('episode_id'),F.col('s.segment_id').alias('segment_id'),
            F.col('d.source').alias('source'),F.col('d.doc_id').alias('doc_id'),
            F.col('s.entry.term').alias('term'),(F.col('s.entry.weight')*F.col('d.entry.weight')).alias('contribution'))
        pairs=products.groupBy('episode_id','segment_id','source','doc_id').agg(
            F.sum('contribution').alias('score'),
            F.slice(F.sort_array(F.collect_list(F.struct('contribution','term')),asc=False),1,5).alias('terms'))
        window=Window.partitionBy('episode_id','segment_id').orderBy(F.desc('score'),F.asc('source'),F.asc('doc_id'))
        top=pairs.filter(F.col('score')>0).withColumn('rank',F.row_number().over(window)).filter(F.col('rank')<=top_k)
        for row in top.orderBy('episode_id','segment_id','rank').toLocalIterator():
            yield {'episode_id':row.episode_id,'segment_id':row.segment_id,'source':row.source,'doc_id':row.doc_id,
                   'score':min(1.0,float(row.score)),'shared_terms':[vocabulary[t.term] for t in row.terms]}
    finally:
        inverted.unpersist()


def run(db, spark, max_documents=20000, max_segments=500):
    documents=list(db.documents.find({'mining.batch_id':{'$exists':True}}, {'_id':0}).limit(max_documents+1))
    # Bound loaded text as well as Spark input; do not silently truncate.
    episodes=[]; segment_count=0
    for e in db.podcast_episodes.find({'status':'ready'}, {'_id':0}):
        segment_count+=len(e.get('analysis',{}).get('segments',[]))
        if segment_count>max_segments: raise ValueError('segment_limit_exceeded')
        episodes.append(e)
    if len(documents)>max_documents: raise ValueError('document_limit_exceeded')
    if not documents or not segment_count: raise ValueError('documents_and_ready_segments_required')
    doc_hash={(d['source'],d['doc_id']):document_fingerprint(d) for d in documents}
    versions=[{'episode_id':e['episode_id'],'batch_id':e.get('batch_id'),'fingerprint':episode_fingerprint(e)} for e in episodes]
    run_id=uuid.uuid4().hex
    report={'run_id':run_id,'algorithm':ALGORITHM,'status':'running','started_at':datetime.now(timezone.utc),
            'document_count':len(documents),'segment_count':segment_count,'episodes':versions,
            'corpus_sha256':fingerprint(sorted((s,i,h) for (s,i),h in doc_hash.items())),
            'spark_application_id':spark.sparkContext.applicationId,'spark_master':spark.sparkContext.master,
            'parameters':{'vocab_size':20000,'max_df':.95,'top_k':3,'max_documents':max_documents,'max_segments':max_segments},
            'link_count':0}
    db.linking_runs.create_index('run_id',unique=True)
    db.cross_source_links.create_index([('run_id',1),('episode_id',1),('segment_id',1),('source',1),('doc_id',1)],unique=True)
    db.linking_runs.insert_one(dict(report)); started=time.monotonic()
    try:
        for item in rank_links(spark,documents,episodes):
            record={**item,'run_id':run_id,'document_fingerprint':doc_hash[(item['source'],item['doc_id'])]}
            db.cross_source_links.insert_one(record); report['link_count']+=1
        report.update(status='success',elapsed_seconds=round(time.monotonic()-started,3))
        db.linking_runs.update_one({'run_id':run_id},{'$set':report})
        db.linking_state.update_one({'_id':'current'},{'$set':{'run_id':run_id}},upsert=True)
    except Exception as exc:
        report.update(status='failed',error=type(exc).__name__,elapsed_seconds=round(time.monotonic()-started,3))
        db.linking_runs.update_one({'run_id':run_id},{'$set':report})
        raise
    return report


def for_episode(db, episode):
    pointer=db.linking_state.find_one({'_id':'current'})
    report=db.linking_runs.find_one({'run_id':pointer['run_id'],'status':'success'}) if pointer else None
    if not report: return {'status':'pending','run_id':None,'items':[]}
    version=next((v for v in report['episodes'] if v['episode_id']==episode['episode_id']),None)
    if not version or version['fingerprint']!=episode_fingerprint(episode) or episode.get('status')!='ready':
        return {'status':'stale','run_id':report['run_id'],'items':[]}
    links=list(db.cross_source_links.find({'run_id':report['run_id'],'episode_id':episode['episode_id']},{'_id':0}))
    keys=[{'source':r['source'],'doc_id':r['doc_id']} for r in links]
    docs={(d['source'],d['doc_id']):d for d in db.documents.find({'$or':keys})} if keys else {}
    items=[]
    for link in links:
        doc=docs.get((link['source'],link['doc_id']))
        if not doc or document_fingerprint(doc)!=link['document_fingerprint']: continue
        items.append({**{k:link[k] for k in ('segment_id','source','doc_id','score','shared_terms')},
                      'title':doc.get('title',''),'source_url':doc.get('source_url','')})
    items.sort(key=lambda r:(r['segment_id'],-r['score'],r['source'],r['doc_id']))
    return {'status':'ready' if items else 'empty','run_id':report['run_id'],'items':items}


def main():
    from pymongo import MongoClient
    from pyspark.sql import SparkSession
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27017')
    parser.add_argument('--mongo-db',required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    spark=SparkSession.builder.master(os.environ.get('SPARK_MASTER','local[2]')).appName('knowpipe-linking').config('spark.sql.shuffle.partitions','4').getOrCreate()
    try:
        with MongoClient(a.mongo_uri,serverSelectionTimeoutMS=5000) as c: report=run(c[a.mongo_db],spark)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    finally: spark.stop()


if __name__=='__main__': main()
