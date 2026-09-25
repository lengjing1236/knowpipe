#!/usr/bin/env python3
"""Real browser -> production worker -> Spark -> full translation -> read/recompute.

Uses an isolated, explicitly small real-source corpus. Does not insert ready jobs
or offline ranking results. Full-background quality is evaluated separately.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from flask import request, session
from knowpipe.web.app import create_app
from knowpipe.web import auth, mongo_sink
from knowpipe.recommendations.importer import import_record
from knowpipe.learning.content import content_view, is_chinese


# Feature 009 translation contract, also used by RecommendationWorker.prepare_chinese.
TRANSLATION_ATTEMPT_LIMIT = 3


def translation_observation(item, doc, *, now=None):
    """Inspect a single version-bound attempt without treating one failure as final."""
    now = now or datetime.now(timezone.utc)
    doc = doc or {}
    view = content_view(doc, include_text=False)
    stage = view['processing']['translation']
    attempts = doc.get('translation_attempts') or {}
    retry_at = attempts.get('retry_at')
    if isinstance(retry_at, datetime):
        retry_at = retry_at.replace(tzinfo=timezone.utc) if retry_at.tzinfo is None else retry_at
    current_version = (view['content_status'] == 'fulltext'
                       and view['content_version'] == item['content_version'])
    current_attempt = bool(current_version and doc.get('translation_expected_processor')
        and attempts.get('source_version') == item['content_version']
        and attempts.get('processor_id') == doc['translation_expected_processor'])
    count = attempts.get('count', 0)
    if not current_version:
        phase = 'content_changed_or_missing'
    elif view['chinese_ready']:
        phase = 'ready'
    elif stage['status'] == 'running':
        # The third attempt can still succeed; its counter is not a completion signal.
        phase = 'running'
    elif stage['status'] == 'unavailable':
        phase = 'unavailable'
    elif stage['status'] == 'failed':
        if current_attempt and count >= TRANSLATION_ATTEMPT_LIMIT:
            phase = 'exhausted'
        elif current_attempt and isinstance(retry_at, datetime) and retry_at > now:
            phase = 'retry_wait'
        else:
            phase = 'retry_pending'
    else:
        phase = 'pending'
    return {'source': item['source'], 'doc_id': item['doc_id'],
            'selected_content_version': item['content_version'], 'content_version': view['content_version'],
            'content_status': view['content_status'], 'language': view['language'],
            'requires_translation': not is_chinese(view['language']),
            'chinese_ready': current_version and view['chinese_ready'], 'status': stage['status'],
            'error_code': stage.get('error_code'), 'phase': phase,
            'processor_id': view['translation_processor'],
            'attempts': {'count': count, 'limit': TRANSLATION_ATTEMPT_LIMIT,
                         'source_version': attempts.get('source_version'),
                         'processor_id': attempts.get('processor_id'), 'matches_current': current_attempt,
                         'retry_at': retry_at.isoformat() if isinstance(retry_at, datetime) else None},
            'quality': view['translation_quality']}


def translation_wait_decision(documents, *, timed_out=False, previous_documents=None):
    foreign = [doc for doc in documents if doc['requires_translation']]
    if not foreign:
        return 'no_translation_candidate'
    if any(doc['phase'] == 'ready' for doc in foreign):
        return 'ready'
    if all(doc['phase'] in {'exhausted', 'unavailable'} for doc in foreign):
        # Worker writes the attempt counter before claiming the translation.
        # Re-read a stable terminal observation after the polling delay rather
        # than failing on a transient old `failed` + new third-attempt counter.
        if previous_documents == documents:
            return 'terminal_failure'
        if not timed_out:
            return 'confirming_terminal_failure'
    if timed_out:
        return 'timeout_running' if any(doc['phase'] == 'running' for doc in foreign) else 'timeout_pending'
    return 'waiting'


def record_translation_observation(report, documents, *, timed_out=False):
    events = report.setdefault('translation_observations', [])
    decision = translation_wait_decision(documents, timed_out=timed_out,
                                        previous_documents=events[-1]['documents'] if events else None)
    if not events or events[-1]['documents'] != documents or events[-1]['decision'] != decision:
        events.append({'observed_at': datetime.now(timezone.utc).isoformat(),
                       'decision': decision, 'documents': documents,
                       'scope': 'Sequential read-only observations; not an atomic multi-document snapshot.'})
    report['translation_wait_outcome'] = decision
    return decision


def require_fresh_output(out):
    names = ('browser.json', 'browser-worker.log', 'mvp-desktop.png', 'mvp-mobile.png', 'mvp-failure.png')
    if any((out / name).exists() for name in names):
        raise FileExistsError('acceptance_evidence_exists; select a new --output directory')
    out.mkdir(parents=True, exist_ok=True)


def capture_failure_page(page, out, report):
    """Capture while Playwright is still alive; screenshot failure cannot hide the cause."""
    try:
        if page is None or page.is_closed():
            report['failure_screenshot'] = {'status': 'unavailable', 'reason': 'page_not_open'}
            return
        path = out / 'mvp-failure.png'
        page.screenshot(path=str(path), full_page=True, timeout=10000)
        report['failure_screenshot'] = {'status': 'saved', 'path': str(path)}
    except Exception as error:
        report['failure_screenshot'] = {'status': 'unavailable', 'error_type': type(error).__name__}


@contextmanager
def acceptance_page(settings, out, report):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=settings.get('executable'),
                                    args=settings.get('args', []))
        page = None
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            yield page
        except Exception:
            capture_failure_page(page, out, report)
            raise
        finally:
            browser.close()


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--corpus', default='state/feature011/evaluation/selected-documents.jsonl')
    parser.add_argument('--output', default='evidence/011-mvp-recommendation-validation')
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--goal', default='理解 PostgreSQL 序列化失败和唯一键冲突分别在什么情况下应该重试事务',
                        help='实际提交给页面的目标；额外目标仅用于工程闭环，不代替冻结质量验收')
    parser.add_argument('--browser-settings', default=os.environ.get('KNOWPIPE_BROWSER_SETTINGS'),
                        help='可选 Chromium executable/args JSON；默认使用 Playwright 已安装的浏览器')
    args = parser.parse_args()
    out = Path(args.output)
    require_fresh_output(out)
    report = {'status': 'running', 'scope': 'Real production worker and browser; small frozen real-source corpus, not full-background quality proof',
              'ready_job_injection': False, 'responses': [], 'page_errors': [], 'stages': [],
              'goal': args.goal, 'learning_quality_verified': False}
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    name = 'knowpipe_mvp011_' + uuid.uuid4().hex
    db = client[name]
    server = process = None
    worker_log = None
    started = time.monotonic()
    try:
        client.admin.command('ping')
        for line in Path(args.corpus).read_text().splitlines():
            import_record(db, json.loads(line))
        report['document_count'] = db.documents.count_documents({})
        uid, password = 'mvp011', uuid.uuid4().hex
        mongo_sink.create_user(db, uid, uid, auth.hash_password(password))
        app = create_app(db=db, secret_key=uuid.uuid4().hex)
        @app.after_request
        def trace(response):
            if request.path.startswith('/api/'):
                report['responses'].append({'path': request.path, 'status': response.status_code,
                    'has_user': bool(session.get('user_id')), 'cookie_present': bool(request.cookies.get(app.config['SESSION_COOKIE_NAME']))})
            return response
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = dict(os.environ)
        env.setdefault('KNOWPIPE_SEMANTIC_MODEL_PATH', 'state/feature011/semantic-multilingual')
        if not any(env.get(name) for name in ('KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH', 'KNOWPIPE_NLLB_MODEL_PATH', 'KNOWPIPE_TRANSLATION_MODEL_PATH')):
            raise RuntimeError('explicit_translation_provider_required')
        env.setdefault('SPARK_MASTER', 'local[1]')
        env.setdefault('PYSPARK_SUBMIT_ARGS', '--driver-memory 1g --conf spark.sql.shuffle.partitions=4 pyspark-shell')
        worker_log = (out / 'browser-worker.log').open('w')
        process = subprocess.Popen([sys.executable, '-m', 'knowpipe.recommendations.worker',
            '--mongo-uri', args.mongo_uri, '--mongo-db', name, '--index-root', 'state/feature011/browser-index'],
            env=env, stdout=worker_log, stderr=subprocess.STDOUT)
        settings = json.loads(Path(args.browser_settings).read_text()) if args.browser_settings else {}
        with acceptance_page(settings, out, report) as page:
            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base + '/login')
            page.locator('#username').fill(uid); page.locator('#password').fill(password)
            page.get_by_role('button', name='登录', exact=True).click(); page.wait_for_url(base + '/')
            page.goto(base + '/learning')
            page.locator('#learning-goal').fill(args.goal)
            page.locator('#goal-form button[type=submit]').click()
            report['stages'].append('goal_saved_in_browser')

            def wait_job(previous_id=None, require_translation=False):
                deadline = time.monotonic() + args.timeout
                observations = []
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError('worker_exited')
                    profile = db.user_profiles.find_one({'user_id': uid}) or {}
                    job = db.recommendation_jobs.find_one({'_id': profile.get('desired_recommendation_job')})
                    if job and job['_id'] != previous_id and job['status'] in {'ready', 'empty'}:
                        if not require_translation:
                            return job
                        if not job['result'].get('items'):
                            raise AssertionError('real_worker_returned_no_candidates')
                        samples = [(item, db.documents.find_one({'source': item['source'], 'doc_id': item['doc_id']}))
                                   for item in job['result']['items']]
                        observations = [translation_observation(item, doc) for item, doc in samples]
                        decision = record_translation_observation(report, observations)
                        if decision == 'ready':
                            for (item, doc), observed in zip(samples, observations):
                                if observed['requires_translation'] and observed['phase'] == 'ready':
                                    return job, item, content_view(doc)
                        if decision == 'no_translation_candidate':
                            raise AssertionError('no_english_recommendation_to_verify_translation')
                        if decision == 'terminal_failure':
                            raise AssertionError('selected_english_translations_exhausted_or_unavailable')
                    if job and job['status'] == 'failed':
                        raise RuntimeError('worker_job_failed:' + str(job.get('error_code')))
                    page.wait_for_timeout(2000)
                if require_translation and observations:
                    outcome = record_translation_observation(report, observations, timed_out=True)
                    if outcome == 'terminal_failure':
                        raise AssertionError('selected_english_translations_exhausted_or_unavailable')
                    raise TimeoutError('translation_' + outcome)
                raise TimeoutError('worker_or_translation_not_ready')

            first = wait_job()
            report['first_result'] = first['result']
            report['first_job'] = first['_id']
            assert first['result'].get('semantic', {}).get('status') in {'ready', 'partial'}, 'semantic_algorithm_not_executed'
            report['stages'].append('production_recommendation_ready')
            assert first['result'].get('items'), 'no_results_to_read'
            first, item, view = wait_job(require_translation=True)
            report['read_document'] = {key: item[key] for key in ('source', 'doc_id', 'content_version', 'title')}
            report['translation'] = {'characters': len(view['chinese_text']), 'processor_id': view['translation_processor'],
                                      'segments': len(view['translation_segments']), 'semantic_verified': False}
            page.reload()
            expect(page.locator('#recommendation-items .card').first).to_be_visible(timeout=30000)
            card = page.locator('#recommendation-items .card').filter(has=page.get_by_role('heading', name=item['title'], exact=True))
            card.locator('summary').first.click()
            card.get_by_role('button', name='阅读中文全文', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            expect(page.locator('#read-count')).to_have_text('0')
            expect(page.get_by_text('逐段核对中文与原文', exact=True)).to_be_visible()
            page.get_by_text('逐段核对中文与原文', exact=True).click()
            expect(page.locator('.aligned-paragraph').first).to_be_visible()
            page.screenshot(path=str(out / 'mvp-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert page.locator('#reading-dialog').evaluate('element => element.scrollWidth <= element.clientWidth')
            page.screenshot(path=str(out / 'mvp-mobile.png'))
            report['stages'].append('full_chinese_reading_and_alignment')
            page.locator('#reading-dialog').get_by_role('button', name='标记已读', exact=True).click()
            expect(page.locator('#read-count')).to_have_text('1', timeout=30000)
            page.locator('#reading-close').click()
            second = wait_job(previous_id=first['_id'])
            assert second['result'].get('semantic', {}).get('status') in {'ready', 'partial'}, 'read_recomputation_degraded'
            assert not any((x['source'], x['doc_id']) == (item['source'], item['doc_id']) for x in second['result'].get('items', []))
            report['second_job'] = second['_id']; report['second_result'] = second['result']
            report['stages'].append('explicit_read_triggered_recomputation')
            assert not report['page_errors'], report['page_errors']
            assert not [r for r in report['responses'] if r['status'] in (401, 403)], 'unexpected_auth_failure'
            report.update(status='passed', opening_did_not_mark_read=True, no_stale_read_document=True,
                          no_mobile_overflow=True, no_auth_failure=True,
                          semantic_algorithm_executed=True, learning_quality_verified=False)
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error)[:300])
        raise
    finally:
        report['seconds'] = time.monotonic() - started
        if process:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        if worker_log: worker_log.close()
        if server: server.shutdown()
        (out / 'browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        client.drop_database(name); client.close()


if __name__ == '__main__':
    main()
