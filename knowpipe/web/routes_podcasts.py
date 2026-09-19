"""Authenticated subscription management and persistent completion notifications."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from bson import ObjectId
from flask import Blueprint, Response, current_app, jsonify, request

from ..podcasts import store
from ..linking.job import for_episode
from ..podcasts.network import FetchError, public_target
from . import auth

podcast_bp = Blueprint('podcasts', __name__, url_prefix='/api')


def db():
    return current_app.config['MONGO_DB']


def login_required(fn):
    return auth.login_required(db)(fn)


def serial(value):
    if isinstance(value, dict):
        return {('id' if key == '_id' else key): serial(item) for key, item in value.items() if key != 'user_id'}
    if isinstance(value, list):
        return [serial(item) for item in value]
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    return value


@podcast_bp.get('/podcasts/subscriptions')
@login_required
def subscriptions():
    return jsonify(items=serial(list(db().podcast_feeds.find(
        {'feed_id': {'$in': store.feed_ids(db(), auth.current_user_id())}}, {'_id': 0}))))


@podcast_bp.post('/podcasts/subscriptions')
@login_required
def add_subscription():
    url = request.json.get('url')
    try:
        public_target(url)
        parts = urlsplit(url)
        url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))
        feed_id = store.subscribe(db(), auth.current_user_id(), url)
    except (FetchError, ValueError) as exc:
        return jsonify(error='subscription_limit' if str(exc) == 'subscription_limit' else 'invalid_feed_url'), 409 if str(exc) == 'subscription_limit' else 400
    return jsonify(feed_id=feed_id), 201


@podcast_bp.delete('/podcasts/subscriptions/<feed_id>')
@login_required
def remove_subscription(feed_id):
    store.unsubscribe(db(), auth.current_user_id(), feed_id)
    return jsonify({})


@podcast_bp.get('/podcasts/episodes')
@login_required
def episodes():
    return jsonify(items=serial(store.list_episodes(db(), auth.current_user_id())))


@podcast_bp.get('/podcasts/episodes/<episode_id>')
@login_required
def episode_detail(episode_id):
    episode = store.owned_episode(db(), auth.current_user_id(), episode_id)
    if episode is None:
        return jsonify(error='not_found'), 404
    episode['cross_source'] = for_episode(db(), episode)
    return jsonify(serial(episode))


@podcast_bp.post('/podcasts/episodes/<episode_id>/transcript')
@login_required
def supply_transcript(episode_id):
    episode = store.owned_episode(db(), auth.current_user_id(), episode_id)
    if episode is None:
        return jsonify(error='not_found'), 404
    text = request.json.get('text')
    if not isinstance(text, str) or not 20 <= len(text.strip()) <= 400000:
        return jsonify(error='invalid_transcript'), 400
    result = db().podcast_episodes.update_one(
        {'episode_id': episode_id, 'status': {'$in': ['awaiting_transcript', 'failed']}},
        {'$set': {'transcript': text.strip(), 'transcript_origin': 'user_supplied',
                  'status': 'queued', 'attempts': 0, 'retry_at': store.now(), 'error_code': None}})
    if not result.matched_count:
        return jsonify(error='episode_not_editable'), 409
    return jsonify(status='queued'), 202


@podcast_bp.get('/notifications')
@login_required
def notifications():
    return jsonify(items=serial(list(db().notifications.find({'user_id': auth.current_user_id()})
                                    .sort('_id', -1).limit(50))))


@podcast_bp.post('/notifications/<notification_id>/read')
@login_required
def read_notification(notification_id):
    if not ObjectId.is_valid(notification_id):
        return jsonify(error='not_found'), 404
    result = db().notifications.update_one({'_id': ObjectId(notification_id), 'user_id': auth.current_user_id()},
                                          {'$set': {'read': True}})
    return (jsonify({}), 200) if result.matched_count else (jsonify(error='not_found'), 404)


@podcast_bp.get('/notifications/stream')
@login_required
def notification_stream():
    database, user_id = db(), auth.current_user_id()
    last = request.headers.get('Last-Event-ID')
    if last and not ObjectId.is_valid(last):
        return jsonify(error='invalid_cursor'), 400
    duration = current_app.config.get('SSE_DURATION_SECONDS', 25)

    def generate():
        cursor = ObjectId(last) if last else None
        deadline = time.monotonic() + duration
        yield 'retry: 3000\n\n'
        while True:
            query = {'user_id': user_id}
            if cursor:
                query['_id'] = {'$gt': cursor}
                records = list(database.notifications.find(query).sort('_id', 1).limit(50))
            else:
                records = list(database.notifications.find(query).sort('_id', -1).limit(50))[::-1]
            for item in records:
                cursor = item['_id']
                yield f'id: {cursor}\nevent: notification\ndata: {json.dumps(serial(item), ensure_ascii=False)}\n\n'
            yield ': heartbeat\n\n'
            if time.monotonic() >= deadline:
                break
            time.sleep(2)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})
