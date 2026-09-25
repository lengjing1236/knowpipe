"""One renewable-leased worker. Web requests never launch Spark or fetch RSS."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import uuid
from datetime import timedelta, timezone

from ..mining.batch import BatchStats, new_batch_id
from ..learning.providers import TextResult, ProviderUnavailable, UnconfiguredProvider
from .learning import publish_episode, transcript_language
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
            payload, _ = (fetch(feed['url'], max_bytes=16 * 1024 * 1024) if fetch is fetch_bytes else fetch(feed['url']))
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
                    **item, 'status': 'queued' if item['transcript_url'] or item.get('audio_url') else 'awaiting_transcript',
                    'attempts': 0, 'retry_at': store.now(), 'created_at': store.now()}}, upsert=True)
                # Publishers sometimes attach the transcript after releasing the audio.
                if item['transcript_url']:
                    db.podcast_episodes.update_one({'episode_id': item['episode_id'],
                        'status': {'$in': ['awaiting_transcript', 'failed', 'queued', 'ready']},
                        'transcript_url': {'$ne': item['transcript_url']}},
                        {'$set': {'transcript_url': item['transcript_url'], 'transcript_type': item['transcript_type'],
                                  'transcript_origin': 'publisher', 'status': 'queued', 'attempts': 0,
                                  'retry_at': store.now(), 'error_code': None}, '$unset': {'transcript': ''}})
            update.update(title=title, last_error=None)
        except (ValueError, FetchError):
            update['last_error'] = 'feed_unavailable'
        check_lease()
        db.podcast_feeds.update_one({'feed_id': feed['feed_id']}, {'$set': update})


def process_episode(db, episode, fetch, analyze, check_lease, transcriber=None, audio_fetch=None):
    from .audio import transcribe_url
    from pymongo import ReturnDocument
    batch = BatchStats(new_batch_id(), ['podcast'])
    batch.input_count = 1
    token, claimed_at = uuid.uuid4().hex, store.now()
    claimed = db.podcast_episodes.find_one_and_update(
        {'episode_id': episode['episode_id'], 'status': {'$in': ['queued', 'failed']}},
        {'$set': {'status': 'processing', 'batch_id': batch.batch_id, 'attempt_token': token,
                  'processing_stage': 'transcription', 'claimed_at': claimed_at}, '$inc': {'attempts': 1}},
        return_document=ReturnDocument.AFTER)
    if not claimed:
        return
    attempt = {'episode_id': episode['episode_id'], 'status': 'processing', 'attempt_token': token}
    db.batches.update_one({'batch_id': batch.batch_id}, {'$set': batch.to_dict()}, upsert=True)
    try:
        text = episode.get('transcript')
        origin = episode.get('transcript_origin', 'publisher')
        language = episode.get('language')
        if not text and episode.get('transcript_url'):
            try:
                payload, actual_type = fetch(episode['transcript_url'])
                text = parse_transcript(payload, episode.get('transcript_type') or actual_type)
            except (ValueError, FetchError):
                if not episode.get('audio_url'):
                    raise
        if not text:
            if not episode.get('audio_url'):
                raise ValueError('missing_audio_or_transcript')
            if audio_fetch is None and (transcriber is None or isinstance(transcriber, UnconfiguredProvider)):
                raise ProviderUnavailable('transcription_not_configured')
            provider = transcriber or UnconfiguredProvider()
            if hasattr(provider, 'with_context'):
                feed = db.podcast_feeds.find_one({'feed_id': episode['feed_id']}) or {}
                provider = provider.with_context(title=episode.get('title', ''), feed_title=feed.get('title', ''))
            result = (audio_fetch or transcribe_url)(episode['audio_url'], provider)
            if not isinstance(result, TextResult) or result.complete is not True or not result.text.strip():
                raise ValueError('incomplete_transcription')
            text, language, origin = result.text, result.language, 'asr'
        language = transcript_language(text, language)
        # Optional legacy analysis remains available to callers; production indexing is
        # performed once in the shared recommendation Spark worker.
        analysis = analyze(text) if analyze else None
        check_lease()
        if not db.podcast_episodes.find_one(attempt):
            raise RuntimeError('worker_lease_lost')
        doc = publish_episode(db, episode, text, language, origin, token, claimed_at, check_lease)
        batch.valid_count = 1
        batch.finish()
        stats = batch.to_dict()
        if analysis:
            stats.update(segment_count=len(analysis['segments']),
                         spark_application_id=analysis['spark_application_id'], spark_master=analysis['spark_master'])
        db.batches.update_one({'batch_id': batch.batch_id}, {'$set': stats}, upsert=True)
        check_lease()
        update = {'status': 'ready', 'processing_stage': 'indexed_pending', 'transcript': text,
                  'transcript_origin': origin, 'language': language, 'content_version': doc['content']['version'],
                  'document_source': 'podcast', 'document_id': episode['episode_id'],
                  'ready_at': episode.get('ready_at') or store.now(), 'error_code': None}
        if analysis:
            update['analysis'] = analysis
        db.podcast_episodes.update_one(attempt, {'$set': update})
    except Exception as exc:
        check_lease()
        logger.warning('Podcast batch %s failed: %s', batch.batch_id, type(exc).__name__)
        known = {'transcript_too_large', 'empty_transcript', 'no_usable_words', 'invalid_transcript',
                 'unsupported_transcript', 'response_too_large', 'non_public_address',
                 'incomplete_transcription', 'missing_audio_or_transcript', 'audio_too_large',
                 'audio_too_long', 'invalid_audio', 'audio_download_failed', 'audio_timeout',
                 'audio_duration_exceeded', 'audio_download_timeout', 'audio_incomplete_download',
                 'audio_fetch_failed', 'audio_decode_failed', 'audio_http_error'}
        code = 'transcription_unavailable' if isinstance(exc, ProviderUnavailable) else (
            str(exc) if isinstance(exc, ValueError) and str(exc) in known else 'processing_failed')
        batch.failed_count = 1
        batch.finish(status='failed', error_message=code)
        db.batches.update_one({'batch_id': batch.batch_id}, {'$set': batch.to_dict()}, upsert=True)
        db.podcast_episodes.update_one(attempt, {'$set': {
            'status': 'failed', 'processing_stage': 'transcription', 'error_code': code,
            'retry_at': store.now() + timedelta(minutes=5)}})


def run_once(db, fetch=fetch_bytes, analyze=None, *, transcriber=None, audio_fetch=None):
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
        # Migrate retained legacy transcripts without fetching or transcribing again.
        db.podcast_episodes.update_many({'feed_id': {'$in': active}, 'status': 'ready',
            'document_id': {'$exists': False}}, {'$set': {'status': 'queued'}})
        db.podcast_episodes.update_many({'feed_id': {'$in': active}, 'status': 'awaiting_transcript',
            'audio_url': {'$type': 'string'}}, {'$set': {'status': 'queued', 'retry_at': store.now()}})
        query = {'feed_id': {'$in': active}, '$or': [{'status': 'queued'},
                 {'status': 'failed', 'attempts': {'$lt': 3}, 'retry_at': {'$lte': store.now()}}]}
        for episode in db.podcast_episodes.find(query).limit(10):
            check_lease()
            process_episode(db, episode, fetch, analyze, check_lease, transcriber, audio_fetch)
        return True
    finally:
        stopped.set()
        keeper.join(timeout=2)
        store.release_lease(db, owner)


def main(argv=None):
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description='Poll RSS, obtain complete transcripts and publish shared learning documents')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    client = MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=5000)
    db = client[os.environ.get('MONGO_DB', 'knowpipe_mining')]
    from ..learning.local_providers import configured_transcriber
    transcriber = configured_transcriber()
    try:
        while True:
            run_once(db, transcriber=transcriber)
            if args.once:
                break
            threading.Event().wait(15)
    finally:
        client.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
