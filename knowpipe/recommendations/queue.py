"""Durable recommendation jobs with version ownership and expiring attempt tokens."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from ..learning.content import content_view
from ..web.mongo_sink import get_or_create_profile
from .engine import PARAMETERS, SELECTION_VERSION

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


def utcnow():
    # Mongo's default codec returns naive UTC datetimes.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_indexes(db):
    db.recommendation_jobs.create_index([('status', 1), ('lease_expires_at', 1), ('created_at', 1)])
    db.recommendation_jobs.create_index([('user_id', 1), ('revision', 1)])
    db.recommendation_runtime.update_one({'_id': 'worker'}, {'$setOnInsert': {
        'generation': 0, 'lease_expires_at': datetime(1970, 1, 1)}}, upsert=True)


def acquire_worker(db, *, now=None):
    now = now or utcnow()
    token = uuid.uuid4().hex
    row = db.recommendation_runtime.find_one_and_update(
        {'_id': 'worker', 'lease_expires_at': {'$lte': now}},
        {'$set': {'owner': token, 'heartbeat_at': now,
                  'lease_expires_at': now + timedelta(seconds=LEASE_SECONDS)}}, return_document=ReturnDocument.AFTER)
    return token if row else None


def _worker_filter(owner, now):
    return {'_id': 'worker', 'owner': owner, 'lease_expires_at': {'$gt': now}}


def renew_worker(db, owner, *, now=None):
    now = now or utcnow()
    return bool(db.recommendation_runtime.update_one(_worker_filter(owner, now), {'$set': {
        'heartbeat_at': now, 'lease_expires_at': now + timedelta(seconds=LEASE_SECONDS)}}).matched_count)


def release_worker(db, owner):
    db.recommendation_runtime.update_one({'_id': 'worker', 'owner': owner}, {'$set': {'lease_expires_at': utcnow()}})


def publish_corpus(db, owner, snapshot, *, now=None):
    now = now or utcnow()
    prior = db.recommendation_runtime.find_one(_worker_filter(owner, now))
    if not prior:
        return None
    update = {'$set': {'corpus': snapshot, 'corpus_checked_at': now}, '$unset': {'error_code': ''}}
    if any(prior.get('corpus', {}).get(key) != snapshot.get(key) for key in ('corpus_id', 'feature_id', 'processing_id')):
        update['$inc'] = {'generation': 1}
    # CAS also prevents simultaneous publication of a lower generation.
    return db.recommendation_runtime.find_one_and_update(
        {**_worker_filter(owner, now), 'generation': prior['generation']}, update, return_document=ReturnDocument.AFTER)


def schedule(db, profile, runtime, *, now=None):
    now = now or utcnow()
    goal = (profile.get('learning_goal') or {}).get('text')
    if not goal or not runtime.get('corpus'):
        return None
    revision = profile.get('learning_revision', 0)
    corpus_id = runtime['corpus']['corpus_id']
    job_id = hashlib.sha256(json.dumps([profile['user_id'], revision, corpus_id, runtime['corpus'].get('feature_id'),
        runtime['corpus'].get('processing_id'), SELECTION_VERSION, PARAMETERS], sort_keys=True).encode()).hexdigest()
    db.recommendation_jobs.update_one({'_id': job_id}, {'$setOnInsert': {
        'user_id': profile['user_id'], 'revision': revision, 'corpus_id': corpus_id,
        'feature_id': runtime['corpus'].get('feature_id'),
        'processing_id': runtime['corpus'].get('processing_id'),
        'algorithm': SELECTION_VERSION, 'parameters': PARAMETERS, 'goal': goal,
        'history': profile.get('read_doc_ids', []), 'status': 'queued', 'attempts': 0,
        'created_at': now}}, upsert=True)
    changed = db.user_profiles.update_one({'user_id': profile['user_id'], 'learning_revision': revision,
        '$or': [{'recommendation_corpus_generation': {'$exists': False}},
                {'recommendation_corpus_generation': {'$lte': runtime['generation']}}]}, {'$set': {
                    'desired_recommendation_job': job_id, 'recommendation_corpus_generation': runtime['generation']}})
    if changed.matched_count:
        # Returning to an earlier immutable corpus revives its superseded task.
        db.recommendation_jobs.update_one({'_id': job_id, 'status': 'stale'},
            {'$set': {'status': 'queued', 'attempts': 0}, '$unset': {'result': '', 'error_code': ''}})
    return job_id if changed.matched_count else None


def claim(db, owner, *, now=None):
    now = now or utcnow()
    runtime = db.recommendation_runtime.find_one(_worker_filter(owner, now))
    if not runtime:
        return None
    db.recommendation_jobs.update_many({'status': 'running', 'attempts': {'$gte': MAX_ATTEMPTS},
        'lease_expires_at': {'$lte': now}}, {'$set': {'status': 'failed', 'error_code': 'attempts_exhausted'}})
    return db.recommendation_jobs.find_one_and_update({'attempts': {'$lt': MAX_ATTEMPTS}, '$or': [
        {'status': 'queued'}, {'status': 'running', 'lease_expires_at': {'$lte': now}}]}, {'$set': {
            'status': 'running', 'attempt_token': uuid.uuid4().hex, 'worker_token': owner,
            'lease_expires_at': runtime['lease_expires_at'], 'started_at': now},
            '$inc': {'attempts': 1}}, sort=[('created_at', 1)], return_document=ReturnDocument.AFTER)


def _attempt_filter(job, now):
    return {'_id': job['_id'], 'status': 'running', 'attempt_token': job['attempt_token'],
            'lease_expires_at': {'$gt': now}}


def renew_job(db, job, *, now=None):
    now = now or utcnow()
    if not renew_worker(db, job['worker_token'], now=now):
        return False
    return bool(db.recommendation_jobs.update_one(_attempt_filter(job, now), {'$set': {
        'lease_expires_at': now + timedelta(seconds=LEASE_SECONDS)}}).matched_count)


def finish(db, job, result, *, now=None):
    now = now or utcnow()
    return bool(db.recommendation_jobs.update_one(_attempt_filter(job, now), {'$set': {
        'status': 'ready' if result.get('items') else 'empty', 'result': result, 'finished_at': now},
        '$unset': {'error_code': ''}}).matched_count)


def fail(db, job, code, *, now=None):
    now = now or utcnow()
    return bool(db.recommendation_jobs.update_one(_attempt_filter(job, now), {'$set': {
        'status': 'failed' if job['attempts'] >= MAX_ATTEMPTS else 'queued', 'error_code': code}}).matched_count)


def is_current(db, job, corpus_id):
    runtime = db.recommendation_runtime.find_one({'_id': 'worker'}) or {}
    feature_id = (runtime.get('corpus') or {}).get('feature_id')
    if feature_id and job.get('feature_id') != feature_id:
        return False
    if job.get('processing_id') != (runtime.get('corpus') or {}).get('processing_id'):
        return False
    return job['corpus_id'] == corpus_id and bool(db.user_profiles.find_one({
        'user_id': job['user_id'], 'learning_revision': job['revision'], 'desired_recommendation_job': job['_id']}))


def request_refresh(db, user_id):
    profile = get_or_create_profile(db, user_id)
    previous_job = db.recommendation_jobs.find_one({'_id': profile.get('desired_recommendation_job'),
        'user_id': user_id, 'revision': profile.get('learning_revision', 0)}) or {}
    db.user_profiles.update_one({'user_id': user_id}, {'$set': {'recommendation_requested_at': utcnow()}})
    # Explicit retry opens a new bounded cycle; ordinary polling never resets attempts.
    db.recommendation_jobs.update_one({'_id': profile.get('desired_recommendation_job'), 'user_id': user_id,
        'revision': profile.get('learning_revision', 0), 'status': 'failed'},
        {'$set': {'status': 'queued', 'attempts': 0}, '$unset': {'error_code': ''}})
    # A transient query-provider failure can have produced a useful original-only
    # result. Explicit refresh retries interpretation without changing the goal.
    db.recommendation_jobs.update_one({'_id': profile.get('desired_recommendation_job'), 'user_id': user_id,
        'revision': profile.get('learning_revision', 0), 'status': {'$in': ['ready', 'empty']},
        '$or': [{'result.query.status': {'$in': ['failed', 'unavailable']}},
                {'result.semantic.status': {'$in': ['failed', 'unavailable', 'partial']}}]},
        {'$set': {'status': 'queued', 'attempts': 0}, '$unset': {'result': '', 'error_code': ''}})
    for item in (previous_job.get('result') or {}).get('items', []):
        db.documents.update_one({'source': item['source'], 'doc_id': item['doc_id'],
            'content.version': item['content_version'], 'processing.translation.status': {'$in': ['failed', 'unavailable', 'running']}},
            {'$unset': {'translation_attempts': ''}})
    return view(db, user_id)


def _public_corpus(runtime):
    corpus = runtime.get('corpus') or {}
    return {key: corpus.get(key) for key in ('corpus_id', 'document_count', 'paragraph_count', 'sources', 'created_at')}


def view(db, user_id):
    profile = get_or_create_profile(db, user_id)
    runtime = db.recommendation_runtime.find_one({'_id': 'worker'}) or {}
    base = {'status': 'needs_goal', 'items': [], 'corpus': _public_corpus(runtime),
            'input_revision': profile.get('learning_revision', 0)}
    if not (profile.get('learning_goal') or {}).get('text'):
        return base
    if runtime.get('error_code'):
        return {**base, 'status': 'failed', 'reason': runtime['error_code']}
    current_id = (runtime.get('corpus') or {}).get('corpus_id')
    job = db.recommendation_jobs.find_one({'_id': profile.get('desired_recommendation_job'),
        'user_id': user_id, 'revision': profile.get('learning_revision', 0), 'corpus_id': current_id})
    if job and (runtime.get('corpus') or {}).get('feature_id') and job.get('feature_id') != runtime['corpus']['feature_id']:
        return {**base, 'status': 'stale', 'reason': 'feature_basis_changed'}
    if job and job.get('processing_id') != (runtime.get('corpus') or {}).get('processing_id'):
        return {**base, 'status': 'stale', 'reason': 'processing_changed'}
    if job and job['status'] in {'ready', 'empty'}:
        result = job['result']
        refs = result.get('items', []) + result.get('history_references', [])
        expected = {(r['source'], r['doc_id']): r['content_version'] for r in refs}
        docs = {}
        if expected:
            for doc in db.documents.find({'$or': [{'source': s, 'doc_id': d} for s, d in expected]}, {'body_raw': 0}):
                docs[(doc['source'], doc['doc_id'])] = content_view(doc, include_text=False)
        if any(key not in docs or docs[key]['content_status'] != 'fulltext' or docs[key]['content_version'] != version
               for key, version in expected.items()):
            return {**base, 'status': 'stale', 'reason': 'content_changed'}
        if not is_current(db, job, current_id):
            return {**base, 'status': 'stale', 'reason': 'profile_changed'}
        items = [{**item, 'chinese_ready': docs[(item['source'], item['doc_id'])]['chinese_ready'],
                  'translation_status': docs[(item['source'], item['doc_id'])]['processing']['translation']['status']}
                 for item in result.get('items', [])]
        from .presentation import localize_items
        items = localize_items(db, items)
        # Baselines are reproducible internal experiment artifacts, not a second user recommendation list.
        return {**base, **{k: result.get(k) for k in ('reason', 'history_used', 'history_unavailable', 'history_relevant_paragraphs', 'query', 'semantic')},
                'status': job['status'], 'items': items, 'completed_at': job.get('finished_at')}
    if runtime.get('lease_expires_at', datetime(1970, 1, 1)) <= utcnow():
        return {**base, 'status': 'waiting_for_worker'}
    if not current_id:
        return {**base, 'status': 'waiting_for_corpus'}
    return {**base, 'status': job['status'] if job else 'queued', 'reason': job.get('error_code') if job else None}
