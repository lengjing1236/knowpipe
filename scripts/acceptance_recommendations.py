"""Real Spark/Mongo/browser acceptance using separately fetched official full texts."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from pymongo import MongoClient
from werkzeug.serving import WSGIRequestHandler, make_server

from knowpipe.learning.content import publish_fulltext
from knowpipe.recommendations import queue
from knowpipe.recommendations.importer import import_file
from knowpipe.recommendations.worker import RecommendationWorker
from knowpipe.web import auth, mongo_sink
from knowpipe.web.app import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://localhost:27017')
    parser.add_argument('--sample', default='state/feature008/sample/documents.jsonl')
    parser.add_argument('--output', default='evidence/008-goal-history-recommendations')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    mongo = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    name = 'knowpipe_acceptance_008_' + uuid.uuid4().hex
    db = mongo[name]
    worker = server = thread = None
    stop = threading.Event()
    result = {'scope': 'real full-text integration, not recommendation effectiveness or 10k acceptance'}
    try:
        count = import_file(db, args.sample)
        app = create_app(db=db, secret_key=uuid.uuid4().hex)
        with ThreadPoolExecutor(max_workers=8) as pool:
            owners = list(pool.map(lambda _: queue.acquire_worker(db), range(24)))
        acquired = [token for token in owners if token]
        assert len(acquired) == 1, acquired
        queue.release_worker(db, acquired[0])
        result['mongo'] = {'version': mongo.server_info()['version'], 'concurrent_worker_claims': 24, 'active_workers': 1}
        os.environ.setdefault('LEARNING_SPARK_PARTITIONS', '2')
        worker = RecommendationWorker(db, 'state/feature008/acceptance-index')
        start = time.monotonic()
        worker.run_once(refresh=True)
        assert worker.index is not None, 'index_not_ready'
        result['index'] = {k: worker.index.snapshot[k] for k in ('corpus_id', 'document_count', 'paragraph_count', 'spark_master', 'spark_application_id', 'sources')}
        result['index']['preparation_seconds'] = round(time.monotonic() - start, 2)
        assert count == result['index']['document_count'] and len(result['index']['sources']) == 2
        print('index_ready', result['index'], flush=True)

        def consume():
            while not stop.is_set():
                worker.run_once()
                stop.wait(1)

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        mongo_sink.create_user(db, 'browser-a', 'learning-a', auth.hash_password('acceptance-password'))
        mongo_sink.create_user(db, 'browser-b', 'learning-b', auth.hash_password('acceptance-password'))
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        errors = []
        settings_path = Path('/tmp/knowpipe-chromium.json')
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
        browser_args = [arg for arg in settings.get('args', []) if arg not in {
            '--disable-web-security', '--allow-running-insecure-content', '--disable-site-isolation-trials'}]
        experiments = []

        def record_experiment(label):
            profile = db.user_profiles.find_one({'user_id': 'browser-a'})
            job = db.recommendation_jobs.find_one({'_id': profile['desired_recommendation_job']})
            data = job['result']
            # Check all visible citations against their bound originals.
            for item in data['items']:
                doc = db.documents.find_one({'source': item['source'], 'doc_id': item['doc_id']})
                ev = item['goal_evidence']
                assert ev['text'] == doc['body_text'][ev['start']:ev['end']]
                if item.get('history_evidence'):
                    prior = item['history_evidence']['history']
                    old = db.documents.find_one({'source': prior['source'], 'doc_id': prior['doc_id']})
                    assert prior['text'] == old['body_text'][prior['start']:prior['end']]
            experiments.append({'label': label, 'goal': job['goal'], 'revision': job['revision'],
                'elapsed_seconds': round((job['finished_at'] - job['started_at']).total_seconds(), 2),
                'history_used': data['history_used'], 'history_unavailable': data['history_unavailable'],
                'items': [{k: item[k] for k in ('source', 'doc_id', 'doc_key', 'title', 'relevance', 'history_overlap', 'rank')} for item in data['items']],
                'baseline': data['baseline'], 'without_history': data['without_history'],
                'without_diversity': data['without_diversity'], 'reason': data['reason']})
            print('experiment_ready', label, [item['title'] for item in data['items']], flush=True)
            return data

        with sync_playwright() as p:
            expect.set_options(timeout=20000)
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('BROWSER_EXECUTABLE') or settings.get('executable'), args=browser_args)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            def page_error(error):
                errors.append(str(error))
                print('browser_page_error', str(error), flush=True)
            page.on('pageerror', page_error)
            page.on('response', lambda response: print('learning_api_error', response.status, response.url, flush=True)
                    if '/api/learning/' in response.url and response.status >= 400 else None)
            page.goto(base + '/login')
            page.locator('#username').fill('learning-a')
            page.locator('#password').fill('acceptance-password')
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(base + '/')
            page.goto(base + '/learning')
            expect(page.locator('#recommendation-status')).to_contain_text('先保存')
            expect(page.locator('#saved-goal')).to_contain_text('还没有')
            page.locator('#learning-goal').fill('数据库事务提交与回滚')
            page.get_by_role('button', name='保存目标', exact=True).click()
            expect(page.locator('#saved-goal')).to_contain_text('数据库事务')
            expect(page.locator('#recommendation-items')).to_have_attribute('data-revision', '1')
            expect(page.locator('#recommendation-status')).to_contain_text('找到', timeout=240000)
            first = record_experiment('database_no_history')
            assert any('transaction' in item['doc_id'] or 'sqlite3' in item['doc_id'] for item in first['items'])
            assert not any(item['doc_id'] in {'library/concurrent.futures.html', 'howto/logging.html'} for item in first['items'])
            first_item = first['items'][0]
            card = page.locator('#recommendation-items .card').first
            card.locator('summary').click()
            page.screenshot(path=str(output / 'recommendations-desktop.png'))
            card.get_by_role('button', name='阅读中文全文', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            assert len(page.locator('#reading-content .transcript').inner_text()) > 1000
            expect(page.locator('#read-count')).to_have_text('0')
            page.locator('#reading-content').get_by_role('button', name='标记已读', exact=True).click()
            expect(page.locator('#read-count')).to_have_text('1')
            page.screenshot(path=str(output / 'reading-desktop.png'))
            page.locator('#reading-close').click()
            expect(page.locator('#recommendation-items')).to_have_attribute('data-revision', '2')
            expect(page.locator('#recommendation-status')).to_contain_text('找到', timeout=240000)
            second = record_experiment('database_with_history')
            assert second['history_used'] == 1
            assert all((item['source'], item['doc_id']) != (first_item['source'], first_item['doc_id']) for item in second['items'])
            page.locator('#recommendation-items .card').first.locator('summary').click()
            page.screenshot(path=str(output / 'history-comparison-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.locator('#recommendation-heading').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'recommendations-mobile.png'))
            page.locator('#learning-goal').fill('Python 异步任务并发')
            page.get_by_role('button', name='保存目标', exact=True).click()
            expect(page.locator('#saved-goal')).to_contain_text('Python 异步')
            expect(page.locator('#recommendation-items')).to_have_attribute('data-revision', '3')
            expect(page.locator('#recommendation-status')).to_contain_text('找到', timeout=240000)
            third = record_experiment('async_with_unrelated_history')
            assert any('async' in item['doc_id'] or 'concurrent' in item['doc_id'] for item in third['items'])
            assert first['items'][0]['doc_id'] != third['items'][0]['doc_id']
            # Stop scheduling before mutation; the API must independently invalidate old evidence.
            stop.set()
            thread.join(timeout=60)
            assert not thread.is_alive()
            victim = third['items'][0]
            original = db.documents.find_one({'source': victim['source'], 'doc_id': victim['doc_id']})
            publish_fulltext(db, victim['source'], victim['doc_id'], '验收期间更新的正文：旧结果必须失效。', 'zh')
            stale = page.request.get(base + '/api/learning/recommendations').json()
            assert stale['status'] == 'stale' and stale['items'] == []
            isolated = queue.view(db, 'browser-b')
            assert isolated['status'] == 'needs_goal' and isolated['items'] == []
            # Restore the exact version, then interrupt only the recommendation
            # read after saving a new goal. Old cards must disappear immediately.
            publish_fulltext(db, victim['source'], victim['doc_id'], original['body_text'], original['language'])
            assert queue.view(db, 'browser-a')['status'] == 'ready'
            page.reload()
            expect(page.locator('#recommendation-status')).to_contain_text('找到')
            page.route('**/api/learning/recommendations', lambda route: route.fulfill(
                status=503, content_type='application/json', body='{"error":"test_network_failure"}'))
            page.locator('#learning-goal').fill('数据库索引设计')
            page.get_by_role('button', name='保存目标', exact=True).click()
            expect(page.locator('#learning-message')).to_contain_text('暂时无法')
            expect(page.locator('#recommendation-items .card')).to_have_count(0)
            page.unroute('**/api/learning/recommendations')
            assert errors == [], errors
            result['browser'] = {'page_errors': errors, 'desktop': [1440, 1000], 'mobile': [390, 844],
                                 'overflow': False, 'chinese_fulltext': True, 'explicit_read_only': True,
                                 'stale_results_hidden': True, 'account_isolation': True,
                                 'network_failure_after_goal_change_hides_old_cards': True}
            browser.close()
        result['experiments'] = experiments
        result['parameters'] = first['parameters']
        result['algorithm'] = first['algorithm']
        context = worker.spark.sparkContext
        result['execution'] = {'spark_master': context.master, 'spark_application_id': context.applicationId,
                               'snapshot_cache_reused': context.applicationId != result['index']['spark_application_id']}
        if context.uiWebUrl:
            prefix = f'{context.uiWebUrl}/api/v1/applications/{context.applicationId}'
            for endpoint in ('jobs', 'stages'):
                with urllib.request.urlopen(prefix + '/' + endpoint, timeout=10) as response:
                    raw = response.read()
                (output / f'spark-{endpoint}.json').write_bytes(raw)
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest = Path(args.sample).with_name('manifest.json')
        if manifest.exists():
            (output / 'sources.json').write_bytes(manifest.read_bytes())
        print('acceptance_passed', flush=True)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=60)
        if worker:
            worker.close()
        if server:
            server.shutdown()
        mongo.drop_database(name)
        mongo.close()


if __name__ == '__main__':
    main()
