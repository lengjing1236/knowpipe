#!/usr/bin/env python3
"""Real Mongo/worker/Spark notification checks with declared RSS transcript replay.

The transport supplies frozen public document text, not a newly discovered
podcast and not ASR. No computed job, ranking, semantic decision, translation,
or notification is injected. Positive and negative branches are both required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluate_mvp011 import load_frozen, digest, write_json, DEFAULT_CASES


def replay_transport(feed_url, document, replay_id):
    transcript_url = feed_url.rsplit('/', 1)[0] + '/transcript.txt'
    published = format_datetime(datetime.now(timezone.utc), usegmt=True)
    rss = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel>'
           '<title>011 controlled transcript replay, not a real podcast</title>'
           '<language>' + escape(document['language']) + '</language><item><guid>' + escape(replay_id) + '</guid>'
           '<title>' + escape(document['title']) + '</title><pubDate>' + escape(published) + '</pubDate>'
           '<link>' + escape(feed_url.rsplit('/', 1)[0] + '/episode') + '</link>'
           '<podcast:transcript url=' + quoteattr(transcript_url) + ' type="text/plain"/>'
           '</item></channel></rss>').encode()
    payloads = {feed_url: (rss, 'application/rss+xml'),
                transcript_url: (document['body_text'].encode(), 'text/plain')}
    requested = []

    def fetch(url):
        requested.append(url)
        if url not in payloads:
            raise ValueError('unexpected_replay_url')
        return payloads[url]
    return fetch, requested, {'rss_sha256': hashlib.sha256(rss).hexdigest(),
                              'transcript_sha256': hashlib.sha256(document['body_text'].encode()).hexdigest()}


def import_reference(db, document):
    from knowpipe.learning.content import publish_fulltext
    keys = {'source': document['source'], 'doc_id': document['doc_id']}
    metadata = {k: document.get(k) for k in ('title', 'source_url', 'license', 'provenance', 'document_type', 'authors', 'tags')}
    db.documents.update_one(keys, {'$set': metadata}, upsert=True)
    return publish_fulltext(db, **keys, text=document['body_text'], language=document['language'])


def actual_job(db, user_id):
    profile = db.user_profiles.find_one({'user_id': user_id}) or {}
    return db.recommendation_jobs.find_one({'_id': profile.get('desired_recommendation_job')})


def decision_check(*, expected, job_status, semantic, current_job, eligible, chinese_ready,
                   notice_count, current_notice_count, idempotent):
    """A failed/partial computation with no notifications cannot pass a negative."""
    completed = bool(job_status in {'ready', 'empty'} and current_job and
                     semantic.get('status') == 'ready' and not semantic.get('error_code'))
    behavior_matches = bool((eligible and chinese_ready and current_notice_count == 1 and notice_count == 1)
                            if expected else (not eligible and notice_count == 0))
    return {'computation_complete': completed, 'behavior_matches': behavior_matches,
            'passed': completed and behavior_matches and idempotent}


def execute_case(client, spark, manifest, documents, replay, run_id, root):
    from knowpipe.learning import store as learning
    from knowpipe.learning.content import content_view
    from knowpipe.learning.providers import processor_identity
    from knowpipe.podcasts import store as podcasts
    from knowpipe.podcasts.worker import run_once as rss_once
    from knowpipe.podcasts.learning import notification_current
    from knowpipe.recommendations.worker import RecommendationWorker
    from knowpipe.recommendations import queue
    from knowpipe.web.mongo_sink import ensure_indexes
    case = next(c for c in manifest['cases'] if c['id'] == replay['base_case'])
    database = 'knowpipe_mvp011_replay_' + run_id + '_' + replay['id']
    if database in client.list_database_names():
        raise ValueError('isolated_database_already_exists')
    db = client[database]
    ensure_indexes(db); learning.ensure_indexes(db); podcasts.ensure_indexes(db)
    user_id = 'replay-' + run_id
    candidate = documents[replay['new_document']]
    feed_url = 'https://mvp-replay.invalid/' + run_id + '/' + replay['id'] + '/feed.xml'
    feed_id = podcasts.subscribe(db, user_id, feed_url)
    for key in case['history']:
        doc = import_reference(db, documents[key])
        learning.mark_read(db, user_id, doc['source'], doc['doc_id'], True, doc['content']['version'])
    learning.save_goal(db, user_id, case['goal_zh'])
    worker = RecommendationWorker(db, str(root / database), spark=spark)
    row = {'id': replay['id'], 'base_case': case['id'], 'database': database,
           'goal_zh': case['goal_zh'], 'history': case['history'],
           'new_material': replay['new_document'], 'new_material_source_url': candidate['source_url'],
           'expected_possible_supplement': replay['expected_possible_supplement'],
           'scope': 'isolated controlled candidate pool and supplied-transcript RSS replay; no live audio or ASR',
           'processors': {'goal': processor_identity(worker.goal_translator),
                          'translation': processor_identity(worker.translator),
                          'semantic': processor_identity(worker.semantic) if worker.semantic else None},
           'status': 'running'}
    started = time.monotonic()
    try:
        worker.run_once(refresh=True)
        before = actual_job(db, user_id)
        row['before_new'] = {'job_id': before['_id'] if before else None,
                             'job_status': before.get('status') if before else None,
                             'result': before.get('result') if before else None,
                             'notification_count': db.notifications.count_documents({'user_id': user_id})}
        if not before or before.get('status') not in {'ready', 'empty'}:
            row.update(status='failed', failure='before_new_computation_not_complete')
            return row
        fetch, requests, transport = replay_transport(feed_url, candidate, replay['id'])
        rss_once(db, fetch=fetch)
        row['transport'] = {**transport, 'requested_urls': requests}
        episode = db.podcast_episodes.find_one({'feed_id': feed_id})
        if not episode:
            row.update(status='failed', failure='rss_episode_missing')
            return row
        row['episode'] = {k: episode.get(k) for k in ('episode_id', 'status', 'transcript_origin', 'language', 'error_code')}
        episode_id = episode['episode_id']
        published = db.documents.find_one({'source': 'podcast', 'doc_id': episode_id})
        if episode['status'] != 'ready' or not published or published.get('body_text') != candidate['body_text'].strip():
            row.update(status='failed', failure='publisher_transcript_not_published_faithfully')
            return row
        # Add origin metadata after production ingestion; never alter text or model input.
        db.documents.update_one({'source': 'podcast', 'doc_id': episode_id}, {'$set': {
            'evaluation_replay': {'synthetic_transport': True, 'original_material': replay['new_document'],
                                  'original_url': candidate['source_url'], 'original_license': candidate.get('license'),
                                  'body_sha256': transport['transcript_sha256']}}})
        worker.run_once(refresh=True)
        job = actual_job(db, user_id)
        result = (job or {}).get('result') or {}
        row['after_new'] = {'job_id': job['_id'] if job else None, 'job_status': (job or {}).get('status'), 'result': result}
        selected = [i for i in result.get('items', []) if i['source'] == 'podcast' and i['doc_id'] == episode_id]
        eligible = bool(selected and selected[0].get('supplement_eligible') and
                        (selected[0].get('comparison') or {}).get('status') == 'possible_supplement')
        doc = db.documents.find_one({'source': 'podcast', 'doc_id': episode_id}) or {}
        view = content_view(doc, include_text=False)
        notices = list(db.notifications.find({'user_id': user_id, 'episode_id': episode_id}))
        current = [n for n in notices if notification_current(db, n)]
        before_ids = [str(n['_id']) for n in notices]
        # Actual worker retry path; no direct call to inject a publish decision.
        worker.run_once()
        repeated = list(db.notifications.find({'user_id': user_id, 'episode_id': episode_id}))
        idempotent = before_ids == [str(n['_id']) for n in repeated]
        expected = replay['expected_possible_supplement']
        check = decision_check(expected=expected, job_status=(job or {}).get('status'),
                               semantic=result.get('semantic') or {},
                               current_job=bool(job and queue.is_current(db, job, worker.index.snapshot['corpus_id'])),
                               eligible=eligible, chinese_ready=view['chinese_ready'], notice_count=len(notices),
                               current_notice_count=len(current), idempotent=idempotent)
        row['decision'] = {'selected': bool(selected), 'supplement_eligible': eligible,
                           'chinese_ready': view['chinese_ready'], 'processing': view['processing'],
                           'notification_count': len(notices), 'current_notification_count': len(current),
                           'notification_idempotent': idempotent, 'expected_decision_met': check['passed'], **check}
        row['index'] = {k: worker.index.snapshot.get(k) for k in ('corpus_id', 'document_count', 'paragraph_count', 'strategy')}
        row['status'] = 'passed_behavior_check_pending_source_review' if check['passed'] else 'failed'
        if not check['computation_complete']:
            row['failure'] = 'new_material_computation_not_complete'
        elif not check['behavior_matches']:
            row['failure'] = ('positive_supplement_not_recommended' if expected and not eligible else
                              'positive_translation_or_notification_missing' if expected else 'negative_not_suppressed')
        if not idempotent:
            row['failure'] = 'notification_idempotency_failed'
        return row
    except Exception as error:
        row.update(status='failed', failure='execution_error', error_type=type(error).__name__)
        return row
    finally:
        row['seconds'] = round(time.monotonic() - started, 3)
        if worker.semantic is not None and hasattr(worker.semantic, 'close'):
            worker.semantic.close()
        worker.spark = None  # shared Spark belongs to main; release only this worker's state.
        worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--output', type=Path, default=ROOT / 'evidence/011-mvp-recommendation-validation/replays.json')
    parser.add_argument('--index-root', type=Path, default=ROOT / 'state/feature011/replay-index')
    parser.add_argument('--case', action='append')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    manifest, documents = load_frozen(args.cases)
    selected = [r for r in manifest['replays'] if not args.case or r['id'] in args.case]
    if not selected or (args.case and set(args.case) != {r['id'] for r in selected}):
        parser.error('unknown_or_empty_replay_selection')
    if args.dry_run:
        print(json.dumps({'manifest_sha256': digest(args.cases), 'replays': selected,
                          'scope': 'controlled supplied-transcript replay; genuine new podcast/ASR explicitly not claimed'}, ensure_ascii=False))
        return
    from pymongo import MongoClient
    from knowpipe.recommendations.runtime import create_spark
    run_id = uuid.uuid4().hex[:12]
    report = {'run_id': run_id, 'manifest_sha256': digest(args.cases), 'status': 'running', 'cases': [],
              'scope': 'actual Mongo, RSS parser/publisher, worker, Spark, providers and notification decisions; supplied source texts in a controlled RSS shell',
              'live_podcast_or_asr': False, 'fake_ready_jobs_or_results': False,
              'limitations': ['controlled small pool, not 10k background', 'agent-prepared expected facts, not human learning outcomes',
                              'positive supplement assertions still need source review even if notification behavior matches']}
    write_json(args.output, report)
    client = spark = None
    try:
        client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        spark = create_spark('knowpipe-011-rss-three-branch-replay', args.index_root)
        for replay in selected:
            row = execute_case(client, spark, manifest, documents, replay, run_id, args.index_root)
            report['cases'].append(row)
            write_json(args.output, report)
            print(row['id'], row['status'], row.get('failure'), row['seconds'], flush=True)
        all_branches = {r['id'] for r in selected} == {r['id'] for r in manifest['replays']}
        report['all_three_branches_executed'] = all_branches
        report['status'] = ('passed_behavior_checks_pending_source_review' if all_branches and
                            all(r['status'].startswith('passed_behavior') for r in report['cases']) else 'failed_or_incomplete')
    except Exception as error:
        report.update(status='execution_failed', error_type=type(error).__name__)
        raise
    finally:
        write_json(args.output, report)
        if spark is not None:
            spark.stop()
        if client is not None:
            client.close()
    if report['status'] != 'passed_behavior_checks_pending_source_review':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
