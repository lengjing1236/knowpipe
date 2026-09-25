#!/usr/bin/env python3
"""Present real recorded query/ranking/translation outputs in an isolated Web DB."""
import argparse
import json
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from flask import request, session
from knowpipe.web.app import create_app
from knowpipe.web import auth, mongo_sink
from knowpipe.learning import store, content
from knowpipe.recommendations import queue
from knowpipe.recommendations.importer import import_record


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *_): pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--output', default='evidence/010-quality-cluster-readiness')
    args = parser.parse_args(); out = Path(args.output)
    recorded = json.loads((out / 'goal-retrieval.json').read_text())
    case = next(r for r in recorded['cases'] if r['id'] == 'db-1' and r['language'] == 'zh')
    result = case['result']; assert result['items']
    media = json.loads((out / 'translation-quality.json').read_text())
    assert media['view']['chinese_ready']  # actual recorded output, including its documented errors
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    name = 'knowpipe_browser010_' + uuid.uuid4().hex; db = client[name]
    server = None
    report = {'scope': 'Web presentation of actual recorded 10k-corpus ranking and model output; isolated selected-document database',
              'status': 'running', 'page_errors': [], 'screens': [], 'requests': [], 'responses': []}
    try:
        wanted = {(i['source'], i['doc_id']) for i in result['items']}
        wanted.add((media['source']['source'], media['source']['doc_id']))
        with open('state/feature009/corpus-expanded/documents.jsonl') as stream:
            for line in stream:
                row = json.loads(line)
                if (row['source'], row['doc_id']) in wanted: import_record(db, row)
        key = {k: media['source'][k] for k in ('source', 'doc_id')}
        doc = db.documents.find_one(key)
        assert content.claim_translation(db, doc, media['processor_id'])
        assert content.publish_translation_quality(db, doc, media['processor_id'], media['view']['translation_quality'])
        assert content.publish_translation(db, **key, source_version=content.content_version(doc),
            text=media['provider_output'], processor_id=media['processor_id'], quality=media['view']['translation_quality'])
        uid = 'browser010'; password = uuid.uuid4().hex
        mongo_sink.create_user(db, uid, uid, auth.hash_password(password))
        store.save_goal(db, uid, result['query']['original']); queue.ensure_indexes(db)
        runtime = {**recorded['corpus'], 'processing_id': 'browser-recorded-010'}
        db.recommendation_runtime.update_one({'_id': 'worker'}, {'$set': {'corpus': runtime, 'generation': 1,
            'lease_expires_at': datetime.utcnow() + timedelta(hours=1)}})
        job_id = queue.schedule(db, db.user_profiles.find_one({'user_id': uid}), db.recommendation_runtime.find_one({'_id': 'worker'}))
        db.recommendation_jobs.update_one({'_id': job_id}, {'$set': {'status': 'ready', 'result': result}})
        app = create_app(db=db, secret_key=uuid.uuid4().hex)
        @app.before_request
        def trace_auth_state():
            if request.path.startswith('/api/'):
                cookie = request.cookies.get(app.config['SESSION_COOKIE_NAME'])
                state = {'path': request.path, 'has_user': bool(session.get('user_id')), 'cookie_present': bool(cookie)}
                if cookie and not state['has_user']:
                    try:
                        _, signed_at = app.session_interface.get_signing_serializer(app).loads(cookie,
                            max_age=int(app.permanent_session_lifetime.total_seconds()), return_timestamp=True)
                        state['signed_at'] = signed_at.isoformat()
                    except Exception as error:
                        state['decode_error_type'] = type(error).__name__
                report['requests'].append(state)
        @app.after_request
        def trace_response(response):
            if request.path.startswith('/api/'):
                report['responses'].append({'path': request.path, 'status': response.status_code})
            return response
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        settings = json.loads(Path('/tmp/knowpipe-chromium.json').read_text())
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=settings['executable'], args=settings['args'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base + '/login'); page.locator('#username').fill(uid); page.locator('#password').fill(password)
            page.get_by_role('button', name='登录', exact=True).click(); page.wait_for_url(base + '/')
            page.goto(base + '/learning')
            expect(page.locator('#recommendation-context')).to_contain_text('自动英文解释', timeout=30000)
            expect(page.locator('#recommendation-context')).to_contain_text('PostgreSQL')
            first = page.locator('#recommendation-items .card').first
            first.locator('summary').click(); expect(first).to_contain_text('技术对象 PostgreSQL')
            page.screenshot(path=str(out / 'query-desktop.png')); report['screens'].append('query-desktop.png')
            # Open the actual full-text translation from the source library, without altering ranking.
            wal = page.locator('#library-items .card').filter(has_text=media['source']['title'])
            if wal.count() == 0:
                page.locator('#next-page').click()
                wal = page.locator('#library-items .card').filter(has_text=media['source']['title'])
            wal.get_by_role('button', name='阅读资料', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            page.get_by_text('自动翻译的检查与局限', exact=True).click()
            expect(page.locator('#reading-content')).to_contain_text('这不保证术语和语义翻译准确')
            expect(page.locator('#reading-content')).to_contain_text(media['processor_id'])
            expect(page.locator('#read-count')).to_have_text('0')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert page.locator('#reading-dialog').evaluate('element => element.scrollWidth <= element.clientWidth')
            page.screenshot(path=str(out / 'quality-mobile.png')); report['screens'].append('quality-mobile.png')
            page.locator('#reading-close').click()
            # A provider failure must remain visible even when the result is empty.
            degraded = {'items': [], 'query': {**result['query'], 'status': 'failed'}, 'reason': 'no_matching_terms'}
            db.recommendation_jobs.update_one({'_id': job_id}, {'$set': {'status': 'empty', 'result': degraded}})
            page.reload(); expect(page.locator('#recommendation-context')).to_contain_text('中文转英文查找目前未就绪')
            assert not report['page_errors'], report['page_errors']
            report.update(status='passed', entity_evidence_visible=True, translation_limitations_visible=True,
                          query_failure_visible=True, mobile_overflow=False, opening_does_not_mark_read=True)
            browser.close()
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        (out / 'browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if server: server.shutdown()
        client.drop_database(name); client.close()


if __name__ == '__main__': main()
