#!/usr/bin/env python3
"""Join actual RSS/ASR output to the complete corpus and run the production worker."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from pyspark.sql import SparkSession
from knowpipe.learning.store import save_goal
from knowpipe.learning.content import content_view
from knowpipe.recommendations.worker import RecommendationWorker
from knowpipe.recommendations.index import load_index
from knowpipe.recommendations import queue
from knowpipe.podcasts.learning import notification_current


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27018')
    parser.add_argument('--evidence',default='evidence/009-fulltext-podcast-learning')
    args=parser.parse_args();out=Path(args.evidence)
    scale=json.loads((out/'scale.json').read_text());media=json.loads((out/'media-acceptance.json').read_text())
    actual=media['technical_podcast']['rss_production_stage']
    client=MongoClient(args.mongo_uri);db=client[scale['database']];source=client[actual['database_name']]
    # Copy previously verified public artifacts and the acceptance subscription
    # timestamp, preserving the actual original version and provenance.
    for collection in ('documents','podcast_feeds','podcast_episodes','podcast_subscriptions'):
        for row in source[collection].find({}, {'_id':0}):
            keys={'documents':('source','doc_id'),'podcast_feeds':('feed_id',),
                  'podcast_episodes':('episode_id',),'podcast_subscriptions':('user_id','feed_id')}[collection]
            db[collection].replace_one({k:row[k] for k in keys},row,upsert=True)
    subscriber=source.podcast_subscriptions.find_one({})
    uid=subscriber['user_id'];goal='ECS Fargate tmpfs'
    save_goal(db,uid,goal)
    snapshot=dict(scale['index'])
    directory=str(Path('state/feature009/scale-index').resolve()/snapshot['corpus_id'])
    snapshot.update(directory=directory,source_path=str(Path(directory)/'documents.jsonl'))
    spark=(SparkSession.builder.master(os.environ.get('SPARK_MASTER','local[1]')).appName('knowpipe-rss-incremental-acceptance')
           .config('spark.sql.shuffle.partitions','16')
           .config('spark.sql.adaptive.coalescePartitions.enabled','false').getOrCreate())
    spark.sparkContext.setLogLevel('WARN')
    worker=RecommendationWorker(db,'state/feature009/scale-index',spark=spark)
    worker.index=load_index(spark,snapshot)
    result={'status':'running','scope':'controlled replay of actual publisher RSS/ASR artifacts into real >=10k corpus; not an independent usefulness label',
            'database':scale['database'],'source_media_database':actual['database_name'],'goal':goal,'user_id':uid,
            'episode_id':actual['episode_id']}
    started=time.monotonic()
    try:
        assert worker.run_once(refresh=True)
        view=queue.view(db,uid)
        assert view['status']=='ready',view
        selected=[x for x in view['items'] if x['source']=='podcast' and x['doc_id']==actual['episode_id']]
        assert selected,'actual_podcast_not_selected'
        assert selected[0]['chinese_ready'],'actual_podcast_translation_not_ready'
        doc=db.documents.find_one({'source':'podcast','doc_id':actual['episode_id']})
        assert content_view(doc)['chinese_ready']
        notice=db.notifications.find_one({'user_id':uid,'episode_id':actual['episode_id'],'kind':'learning_recommendation'})
        assert notice and notification_current(db,notice),'personal_notification_missing'
        worker.run_once()
        assert db.notifications.count_documents({'user_id':uid,'episode_id':actual['episode_id']})==1
        result.update(status='passed',index={k:worker.index.snapshot.get(k) for k in
            ('corpus_id','document_count','paragraph_count','strategy','changed_documents','reused_documents','idf_basis_corpus_id')},
            selected=[{k:x.get(k) for k in ('source','doc_id','title','rank','chinese_ready','reason')} for x in view['items']],
            notification={'kind':notice['kind'],'recommendation_mode':notice['recommendation_mode'],'count':1},
            actual_translation_characters=len(doc['translation']['text']),
            source_version=doc['content']['version'])
        assert result['index']['strategy']=='incremental',result['index']
        assert result['index']['changed_documents']==1,result['index']
        print('rss_incremental_passed',result['index'],flush=True)
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:500]);raise
    finally:
        result['seconds']=round(time.monotonic()-started,2)
        (out/'rss-incremental.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        worker.close();client.close()

if __name__=='__main__':main()
