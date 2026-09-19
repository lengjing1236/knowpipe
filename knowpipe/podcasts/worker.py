"""One renewable-leased worker. Web requests never launch Spark or fetch RSS."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import uuid
from datetime import timedelta, timezone

from ..mining.batch import BatchStats, new_batch_id
from . import store
from .feeds import parse_feed, parse_transcript
from .network import FetchError, fetch_bytes

logger = logging.getLogger(__name__)


def poll_feeds(db, fetch, check_lease):
    active = db.podcast_subscriptions.distinct('feed_id')
    for feed in db.podcast_feeds.find({'feed_id': {'$in': active}, 'next_poll_at': {'$lte': store.now()}}).limit(50):
        check_lease()
        update = {'last_checked_at': store.now(), 'next_poll_at': store.now() + timedelta(seconds=300)}
        try:
            payload, _ = fetch(feed['url'])
            title, items = parse_feed(payload, feed['url'], max_items=100 if feed.get('last_checked_at') else 3)
            latest = db.podcast_episodes.find_one(
                {'feed_id': feed['feed_id'], 'published_at': {'$ne': None}},
                {'published_at': 1}, sort=[('published_at', -1)])
            cutoff = latest['published_at'].replace(tzinfo=timezone.utc) if latest else None
            check_lease()
            for item in items:
                # The second poll must not turn the initial three episodes into an archive import.
                # Existing older episodes still receive late publisher transcript updates.
                if (cutoff and item['published_at'] and item['published_at'] < cutoff
                        and not db.podcast_episodes.find_one({'episode_id': item['episode_id']}, {'_id': 1})):
                    continue
                db.podcast_episodes.update_one({'episode_id': item['episode_id']}, {'$setOnInsert': {
                    **item, 'status': 'queued' if item['transcript_url'] else 'awaiting_transcript',
                    'attempts': 0, 'retry_at': store.now(), 'created_at': store.now()}}, upsert=True)
                # Publishers sometimes attach the transcript after releasing the audio.
                if item['transcript_url']:
                    db.podcast_episodes.update_one({'episode_id': item['episode_id'], 'status': 'awaiting_transcript'},
                        {'$set': {'transcript_url': item['transcript_url'], 'transcript_type': item['transcript_type'],
                                  'status': 'queued', 'retry_at': store.now()}})
            update.update(title=title, last_error=None)
        except (ValueError, FetchError):
            update['last_error'] = 'feed_unavailable'
        check_lease()
        db.podcast_feeds.update_one({'feed_id': feed['feed_id']}, {'$set': update})


def process_episode(db, episode, fetch, analyze, check_lease):
    batch = BatchStats(new_batch_id(), ['podcast'])
    batch.input_count = 1
    db.podcast_episodes.update_one({'episode_id': episode['episode_id']}, {'$set': {
        'status': 'processing', 'batch_id': batch.batch_id}, '$inc': {'attempts': 1}})
    db.batches.update_one({'batch_id': batch.batch_id}, {'$set': batch.to_dict()}, upsert=True)
    try:
        text = episode.get('transcript')
        origin = episode.get('transcript_origin', 'publisher')
        if not text:
            payload, actual_type = fetch(episode['transcript_url'])
            text = parse_transcript(payload, episode.get('transcript_type') or actual_type)
        analysis = analyze(text)
        check_lease()
        batch.valid_count = 1
        batch.finish()
        stats = {**batch.to_dict(), 'segment_count': len(analysis['segments']),
                 'spark_application_id': analysis['spark_application_id'], 'spark_master': analysis['spark_master']}
        # Publish the success record before making the episode visible as ready.
        db.batches.update_one({'batch_id': batch.batch_id}, {'$set': stats}, upsert=True)
        db.podcast_episodes.update_one({'episode_id': episode['episode_id']}, {'$set': {
            'status': 'ready', 'transcript': text, 'transcript_origin': origin, 'analysis': analysis,
            'ready_at': store.now(), 'error_code': None, 'notifications_published': False}})
    except Exception as exc:
        check_lease()
        logger.warning('Podcast batch %s failed: %s', batch.batch_id, type(exc).__name__)
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            'transcript_too_large', 'empty_transcript', 'no_usable_words', 'invalid_transcript',
            'unsupported_transcript', 'response_too_large', 'non_public_address'} else 'processing_failed'
        batch.failed_count = 1
        batch.finish(status='failed', error_message=code)
        db.batches.update_one({'batch_id': batch.batch_id}, {'$set': batch.to_dict()}, upsert=True)
        db.podcast_episodes.update_one({'episode_id': episode['episode_id']}, {'$set': {
            'status': 'failed', 'error_code': code, 'retry_at': store.now() + timedelta(minutes=5)}})


def run_once(db, fetch=fetch_bytes, analyze=None):
    store.ensure_indexes(db)
    owner = uuid.uuid4().hex
    if not store.acquire_lease(db, owner):
        return False
    stopped, lost = threading.Event(), threading.Event()

    def heartbeat():
        while not stopped.wait(15):
            try:
                if not store.renew_lease(db, owner):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    def check_lease():
        if lost.is_set() or not store.renew_lease(db, owner):
            lost.set()
            raise RuntimeError('worker_lease_lost')

    keeper = threading.Thread(target=heartbeat, daemon=True)
    keeper.start()
    try:
        # Acquiring the global lease proves any processing rows belonged to an expired worker.
        for stale in db.podcast_episodes.find({'status': 'processing'}):
            db.batches.update_one({'batch_id': stale.get('batch_id')}, {'$set': {
                'status': 'failed', 'finished_at': store.now(), 'error_message': 'worker_interrupted', 'failed_count': 1}})
        db.podcast_episodes.update_many({'status': 'processing'}, {'$set': {'status': 'queued'}})
        poll_feeds(db, fetch, check_lease)
        active = db.podcast_subscriptions.distinct('feed_id')
        query = {'feed_id': {'$in': active}, '$or': [{'status': 'queued'},
                 {'status': 'failed', 'attempts': {'$lt': 3}, 'retry_at': {'$lte': store.now()}}]}
        for episode in db.podcast_episodes.find(query).limit(10):
            check_lease()
            process_episode(db, episode, fetch, analyze, check_lease)
        for episode in db.podcast_episodes.find({'status': 'ready', 'notifications_published': {'$ne': True}}):
            check_lease()
            store.publish_notifications(db, episode)
            db.podcast_episodes.update_one({'episode_id': episode['episode_id']}, {'$set': {'notifications_published': True}})
        return True
    finally:
        stopped.set()
        keeper.join(timeout=2)
        store.release_lease(db, owner)


def main(argv=None):
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description='Poll subscribed RSS feeds and mine transcripts with Spark')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    client = MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=5000)
    db = client[os.environ.get('MONGO_DB', 'knowpipe_mining')]
    spark = None

    def analyze(text):
        nonlocal spark
        from pyspark.sql import SparkSession
        from .analysis import analyze_transcript
        if spark is None:
            spark = (SparkSession.builder.master(os.environ.get('SPARK_MASTER', 'local[2]'))
                     .appName('knowpipe-podcasts').config('spark.sql.shuffle.partitions', '4').getOrCreate())
        return analyze_transcript(spark, text)

    try:
        while True:
            run_once(db, analyze=analyze)
            if args.once:
                break
            threading.Event().wait(15)
    finally:
        if spark is not None:
            spark.stop()
        client.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
