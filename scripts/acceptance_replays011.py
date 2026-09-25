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


def translation_phase(item, doc, view, *, now=None):
    """Read state after a synchronous worker turn; one failure is not exhaustion."""
    now = now or datetime.now(timezone.utc)
    attempts = doc.get('translation_attempts') or {}
    retry_at = attempts.get('retry_at')
    if isinstance(retry_at, datetime):
        retry_at = retry_at.replace(tzinfo=timezone.utc) if retry_at.tzinfo is None else retry_at
    stage = view['processing']['translation']
    version_matches = view['content_version'] == item['content_version']
    current_attempt = bool(version_matches and doc.get('translation_expected_processor') and
                           attempts.get('source_version') == item['content_version'] and
                           attempts.get('processor_id') == doc['translation_expected_processor'])
    if not version_matches or view['content_status'] != 'fulltext':
        phase = 'content_changed_or_missing'
    elif view['chinese_ready']:
        phase = 'ready'
    elif stage['status'] == 'running':
        phase = 'running'
    elif stage['status'] == 'unavailable':
        phase = 'unavailable'
    elif stage['status'] == 'failed' and current_attempt and attempts.get('count', 0) >= 3:
        phase = 'exhausted'
    elif stage['status'] == 'failed' and current_attempt and isinstance(retry_at, datetime) and retry_at > now:
        phase = 'retry_wait'
    elif stage['status'] == 'failed':
        phase = 'retry_pending'
    else:
        phase = 'pending'
    return {'phase': phase, 'status': stage['status'], 'error_code': stage.get('error_code'),
            'chinese_ready': bool(version_matches and view['chinese_ready']),
            'attempts': attempts.get('count', 0), 'attempt_limit': 3,
            'attempt_matches_current': current_attempt,
            'retry_at': retry_at.isoformat() if isinstance(retry_at, datetime) else None,
            'processor_id': doc.get('translation_expected_processor'),
            'quality': view.get('translation_quality')}


def execute_case(client, spark, manifest, documents, replay, run_id, root, *, translation_timeout=1800):
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
        deadline = time.monotonic() + translation_timeout
        worker.run_once(refresh=True)

        def observe():
            job = actual_job(db, user_id)
            result = (job or {}).get('result') or {}
            selected = [i for i in result.get('items', []) if i['source'] == 'podcast' and i['doc_id'] == episode_id]
            eligible = bool(selected and selected[0].get('supplement_eligible') and
                            (selected[0].get('comparison') or {}).get('status') == 'possible_supplement')
            doc = db.documents.find_one({'source': 'podcast', 'doc_id': episode_id}) or {}
            view = content_view(doc, include_text=False)
            notices = list(db.notifications.find({'user_id': user_id, 'episode_id': episode_id}))
            return job, result, selected, eligible, doc, view, notices

        row['translation_observations'] = []
        while True:
            job, result, selected, eligible, doc, view, notices = observe()
            observation = translation_phase(selected[0], doc, view) if selected else {'phase': 'not_selected'}
            if not row['translation_observations'] or row['translation_observations'][-1]['state'] != observation:
                row['translation_observations'].append({'observed_at': datetime.now(timezone.utc).isoformat(),
                    'state': observation, 'scope': 'after synchronous real worker turn in isolated database'})
                print(replay['id'], 'translation', observation, flush=True)
            if not eligible:
                row['translation_wait_outcome'] = 'not_eligible'
                break
            if observation['phase'] in {'ready', 'exhausted', 'unavailable', 'content_changed_or_missing'}:
                row['translation_wait_outcome'] = observation['phase']
                break
            if time.monotonic() >= deadline:
                row['translation_wait_outcome'] = 'timeout_' + observation['phase']
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
            # Real production retry respects its unchanged 3-attempt/5-minute policy.
            if time.monotonic() < deadline:
                worker.run_once()

        before_ids = sorted(str(n['_id']) for n in notices)
        timed_out = row['translation_wait_outcome'].startswith('timeout_')
        # Retry then re-read all decisions: a pre-retry view cannot describe its result.
        if not timed_out:
            worker.run_once()
        job, result, selected, eligible, doc, view, notices = observe()
        idempotent = (before_ids == sorted(str(n['_id']) for n in notices)) if not timed_out else None
        current = [n for n in notices if notification_current(db, n)]
        row['after_new'] = {'job_id': job['_id'] if job else None, 'job_status': (job or {}).get('status'), 'result': result}
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
        if idempotent is False:
            row['failure'] = 'notification_idempotency_failed'
        if timed_out:
            row['failure'] = 'translation_wait_timeout_not_final_exhaustion'
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
    parser.add_argument('--translation-timeout', type=float, default=1800,
                        help='Per-case wait budget starting before new-material worker turn; in-flight calls finish normally.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.translation_timeout <= 0:
        parser.error('translation-timeout must be positive')
    if not args.dry_run and args.output.exists():
        parser.error('output already exists; use a new evidence path instead of overwriting')
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
              'execution_basis': {'script_sha256': digest(Path(__file__)),
                                  'production_files': {str(path.relative_to(ROOT)): digest(path)
                                                       for path in sorted((ROOT / 'knowpipe').rglob('*.py'))},
                                  'spark_master': os.environ.get('SPARK_MASTER'),
                                  'pyspark_submit_args': os.environ.get('PYSPARK_SUBMIT_ARGS')},
              'translation_timeout_seconds': args.translation_timeout,
              'translation_wait_policy': 'Bounded wait; unchanged production 3 attempts with 5-minute retry delay. In-flight synchronous calls may exceed deadline; record timeout without claiming exhaustion.',
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
            row = execute_case(client, spark, manifest, documents, replay, run_id, args.index_root,
                               translation_timeout=args.translation_timeout)
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
