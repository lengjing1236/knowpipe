"""Isolated acceptance fixture: real Spark + Mongo; synthetic RSS/transcript, no scale claim.

Requires a dedicated MongoDB on localhost:27028. Never writes course databases.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient
from pyspark.sql import SparkSession

from knowpipe.podcasts import store, worker
from knowpipe.podcasts.analysis import analyze_transcript
from knowpipe.web import auth, mongo_sink


def main():
    mongo_uri = 'mongodb://127.0.0.1:27028'
    database_name = 'knowpipe_acceptance_' + uuid.uuid4().hex[:8]
    database = MongoClient(mongo_uri, tz_aware=True)[database_name]
    mongo_sink.ensure_indexes(database)
    store.ensure_indexes(database)
    mongo_sink.create_user(database, 'acceptance-user', 'acceptance', auth.hash_password('acceptance-password123'))
    store.subscribe(database, 'acceptance-user', 'https://example.com/acceptance-feed')
    rss = b'''<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Acceptance fixtures</title>
      <item><guid>spark-demo</guid><title>Synthetic acceptance: Spark and knowledge discovery</title>
      <link>https://example.com/episode</link><pubDate>Wed, 16 Sep 2026 10:00:00 GMT</pubDate>
      <podcast:transcript url="https://example.com/transcript.txt" type="text/plain"/></item>
      <item><guid>missing-demo</guid><title>Awaiting publisher transcript</title></item></channel></rss>'''
    transcript = ('分布式计算将数据分配给多个任务。Spark 使用任务调度和缓存处理数据。MongoDB 保存文档和索引。\n' * 12
                  + '科学播客分享机器学习和教育经验。听众通过文字稿学习知识，关键词帮助发现相关内容。\n' * 12)
    def fetch(url):
        return (rss, 'application/rss+xml') if url.endswith('acceptance-feed') else (transcript.encode(), 'text/plain')
    spark = (SparkSession.builder.master('local[2]').appName('knowpipe-acceptance-podcast')
             .config('spark.sql.shuffle.partitions', '2').getOrCreate())
    try:
        worker.run_once(database, fetch=fetch, analyze=lambda text: analyze_transcript(spark, text))
        worker.run_once(database, fetch=fetch, analyze=lambda text: analyze_transcript(spark, text))
    finally:
        spark.stop()
    ready = database.podcast_episodes.find_one({'status': 'ready'})
    assert ready is not None
    assert database.notifications.count_documents({}) == 1
    assert database.podcast_episodes.count_documents({}) == 2
    manifest = {'mongo_uri': mongo_uri, 'database': database_name, 'episode_id': ready['episode_id'],
                'batch_id': ready['batch_id'], 'spark_application_id': ready['analysis']['spark_application_id'],
                'segments': len(ready['analysis']['segments']), 'notifications': 1,
                'data_kind': 'synthetic acceptance fixture, not course scale evidence',
                'created_at': datetime.now(timezone.utc).isoformat()}
    Path('/tmp/knowpipe-acceptance.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
