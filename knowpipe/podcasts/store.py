"""Persistent subscriptions, idempotent publication and a renewable worker lease."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .feeds import stable_id


def now():
    return datetime.now(timezone.utc)


def ensure_indexes(db):
    db.documents.create_index([('source', 1), ('doc_id', 1)], unique=True)
    db.podcast_feeds.create_index('feed_id', unique=True)
    db.podcast_subscriptions.create_index([('user_id', 1), ('feed_id', 1)], unique=True)
    db.podcast_subscriptions.create_index('feed_id')
    db.podcast_episodes.create_index('episode_id', unique=True)
    db.podcast_episodes.create_index([('feed_id', 1), ('published_at', -1)])
    db.podcast_episodes.create_index([('status', 1), ('retry_at', 1)])
    db.notifications.create_index([('user_id', 1), ('episode_id', 1)], unique=True)
    db.notifications.create_index([('user_id', 1), ('_id', -1)])
    db.batches.create_index('batch_id', unique=True)


def subscribe(db, user_id, url):
    feed_id = stable_id(url)
    # Atomically reserve a slot; concurrent requests cannot exceed the per-user cap.
    db.podcast_quotas.update_one({'_id': user_id}, {'$setOnInsert': {'feed_ids': []}}, upsert=True)
    reserved = db.podcast_quotas.find_one_and_update(
        {'_id': user_id, '$or': [{'feed_ids': feed_id}, {'feed_ids.19': {'$exists': False}}]},
        {'$addToSet': {'feed_ids': feed_id}}, return_document=ReturnDocument.AFTER)
    if reserved is None:
        raise ValueError('subscription_limit')
    db.podcast_feeds.update_one({'feed_id': feed_id}, {'$setOnInsert': {
        'feed_id': feed_id, 'url': url, 'title': url, 'next_poll_at': now(),
        'last_checked_at': None, 'last_error': None}}, upsert=True)
    db.podcast_subscriptions.update_one({'user_id': user_id, 'feed_id': feed_id},
        {'$setOnInsert': {'user_id': user_id, 'feed_id': feed_id, 'created_at': now()}}, upsert=True)
    return feed_id


def unsubscribe(db, user_id, feed_id):
    db.podcast_subscriptions.delete_one({'user_id': user_id, 'feed_id': feed_id})
    db.podcast_quotas.update_one({'_id': user_id}, {'$pull': {'feed_ids': feed_id}})


def feed_ids(db, user_id):
    return db.podcast_subscriptions.distinct('feed_id', {'user_id': user_id})


def list_episodes(db, user_id):
    projection = {'_id': 0, 'transcript': 0, 'analysis': 0}
    items = list(db.podcast_episodes.find({'feed_id': {'$in': feed_ids(db, user_id)}}, projection)
                 .sort([('published_at', -1), ('episode_id', 1)]).limit(50))
    from ..learning.content import content_view
    for episode in items:
        if episode.get('document_id'):
            doc = db.documents.find_one({'source': 'podcast', 'doc_id': episode['document_id']})
            if doc:
                episode['learning_content'] = content_view(doc, include_text=False)
        for internal in ('attempt_token', 'claimed_at'):
            episode.pop(internal, None)
    return items


def owned_episode(db, user_id, episode_id):
    return db.podcast_episodes.find_one({'episode_id': episode_id,
        'feed_id': {'$in': feed_ids(db, user_id)}}, {'_id': 0})


def acquire_lease(db, owner, seconds=90):
    current = now()
    try:
        record = db.podcast_worker_leases.find_one_and_update(
            {'_id': 'podcast-worker', '$or': [{'expires_at': {'$lte': current}}, {'owner': owner}]},
            {'$set': {'owner': owner, 'expires_at': current + timedelta(seconds=seconds)}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return False
    return record['owner'] == owner


def renew_lease(db, owner, seconds=90):
    return db.podcast_worker_leases.update_one(
        {'_id': 'podcast-worker', 'owner': owner, 'expires_at': {'$gt': now()}},
        {'$set': {'expires_at': now() + timedelta(seconds=seconds)}}).matched_count == 1


def release_lease(db, owner):
    db.podcast_worker_leases.delete_one({'_id': 'podcast-worker', 'owner': owner})
