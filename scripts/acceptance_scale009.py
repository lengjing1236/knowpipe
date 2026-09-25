#!/usr/bin/env python3
"""Real >=10k fulltext Mongo/Spark acceptance, isolated DB, no model test doubles."""
from __future__ import annotations
import argparse
import json
import hashlib
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from knowpipe.recommendations.importer import import_file
from knowpipe.recommendations.index import prepare_snapshot, build_index, load_index, mark_indexed
from knowpipe.recommendations.engine import recommend
from knowpipe.learning.content import content_view


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='state/feature009/corpus/documents.jsonl')
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27018')
    parser.add_argument('--output',default='evidence/009-fulltext-podcast-learning')
    parser.add_argument('--index-root',default='state/feature009/scale-index')
    parser.add_argument('--reuse-database',help='Resume a previous isolated acceptance import')
    parser.add_argument('--keep-database',action='store_true',help='Retain isolated public-data database for browser acceptance/demo')
    parser.add_argument('--goal',default='PostgreSQL 事务 transaction rollback')
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    client=MongoClient(args.mongo_uri,serverSelectionTimeoutMS=5000)
    database=args.reuse_database or ('knowpipe_scale009_'+uuid.uuid4().hex)
    if not database.startswith('knowpipe_scale009_'): raise ValueError('acceptance_database_required')
    db=client[database]
    spark=index=None
    result={'database': database, 'scope':'real public fulltext scale and traceable retrieval; not learning-effectiveness proof',
            'goal':args.goal,'status':'running','started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    started=time.monotonic()
    try:
        before=time.monotonic()
        count=db.documents.count_documents({}) if args.reuse_database else import_file(db,args.input)
        digest=hashlib.sha256()
        with open(args.input,'rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
        result['source']={'path':args.input,'sha256':digest.hexdigest()}
        result['mongo']={'version':client.server_info()['version'],'imported':count,'seconds':round(time.monotonic()-before,2),'import_reused':bool(args.reuse_database)}
        assert count>=10000,count
        assert db.documents.count_documents({'content.kind':'fulltext'})==count
        print('imported',result['mongo'],flush=True)
        from pyspark.sql import SparkSession
        spark=(SparkSession.builder.master(os.environ.get('SPARK_MASTER','local[1]'))
            .appName('knowpipe-feature009-scale').config('spark.sql.shuffle.partitions','16').config('spark.sql.adaptive.coalescePartitions.enabled','false')
            .config('spark.ui.retainedJobs','100').config('spark.ui.retainedStages','200').getOrCreate())
        spark.sparkContext.setLogLevel('WARN')
        before=time.monotonic()
        snapshot=build_index(spark,prepare_snapshot(db,args.index_root))
        mark_indexed(db,snapshot)
        result['index']={k:v for k,v in snapshot.items() if k not in {'source_path','directory','created_at'}}
        result['index']['preparation_seconds']=round(time.monotonic()-before,2)
        assert snapshot['document_count']==count and len(snapshot['sources'])>=2
        print('indexed',result['index'],flush=True)
        index=load_index(spark,snapshot)
        before=time.monotonic()
        recommendation=recommend(spark,index,args.goal,[])
        assert recommendation['items'],'no_goal_recommendations'
        for item in recommendation['items']:
            doc=db.documents.find_one({'source':item['source'],'doc_id':item['doc_id']})
            evidence=item['goal_evidence']
            assert doc['body_text'][evidence['start']:evidence['end']]==evidence['text']
            assert content_view(doc,include_text=False)['processing']['analysis']['status']=='ready'
        result['recommendation']={'seconds':round(time.monotonic()-before,2),**recommendation}
        comparisons=[]
        for goal in ('理解操作系统的进程调度原理','operating system process scheduling'):
            before=time.monotonic()
            observed=recommend(spark,index,goal,[])
            comparisons.append({'goal':goal,'seconds':round(time.monotonic()-before,2),
                'items':[{'title':x['title'],'source':x['source'],'doc_id':x['doc_id']} for x in observed['items']],
                'reason':observed.get('reason')})
        result['language_probe']={'scope':'illustrative language-coverage probe, not independently labelled evaluation',
                                  'cases':comparisons}
        result['resources']={'master':spark.sparkContext.master,'application_id':spark.sparkContext.applicationId,
            'driver_memory':spark.sparkContext.getConf().get('spark.driver.memory','default'),
            'note':'single-host local Spark; not a distributed multi-host cluster'}
        if spark.sparkContext.uiWebUrl:
            base=f'{spark.sparkContext.uiWebUrl}/api/v1/applications/{spark.sparkContext.applicationId}'
            for endpoint in ('jobs','stages'):
                with urllib.request.urlopen(base+'/'+endpoint,timeout=15) as response:
                    (output/f'scale-spark-{endpoint}.json').write_bytes(response.read())
        result['status']='passed'
        print('scale_passed',count,'documents',snapshot['paragraph_count'],'paragraphs',len(recommendation['items']),'recommendations',flush=True)
    except Exception as exc:
        result['status']='failed';result['error_type']=type(exc).__name__;result['error']=str(exc)[:500]
        raise
    finally:
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        (output/'scale.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        try:
            if index:index.close()
            if spark:spark.stop()
        except Exception:
            pass  # Preserve the original recorded failure if the JVM already died.
        if not args.keep_database: client.drop_database(database)
        client.close()

if __name__=='__main__':main()
