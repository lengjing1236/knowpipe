"""Isolated real-Mongo and browser acceptance for Feature 007; never seeds project data."""
from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pymongo import MongoClient
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import WSGIRequestHandler, make_server

from knowpipe.learning import content, store
from knowpipe.mining.mongo_sink import ensure_indexes as ensure_mining_indexes
from knowpipe.web import auth, mongo_sink
from knowpipe.web.app import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://localhost:27017')
    parser.add_argument('--output', default='evidence/007-learning-foundation')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    mongo = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
    db_name = 'knowpipe_acceptance_007_' + uuid.uuid4().hex
    db = mongo[db_name]
    server = None
    result = {}
    try:
        ensure_mining_indexes(db)
        app = create_app(db=db, secret_key=uuid.uuid4().hex)
        app.testing = True
        for index in range(16):
            db.documents.insert_one({'source': 'official_docs', 'doc_id': str(index), 'title': f'测试样例：技术资料 {index}',
                                     'source_url': 'https://example.com/learning', 'language': 'zh', 'body_text': ''})
            content.publish_fulltext(db, 'official_docs', str(index), '技术资料测试正文。\n\n    print("示例代码")', 'zh')
        db.documents.update_one({'doc_id': '0'}, {'$set': {'title': '测试样例：Redis 持久化'}})
        content.publish_fulltext(db, 'official_docs', '0',
                                 'Redis 持久化\n\nRDB 保存某个时刻的数据快照；AOF 记录写操作。选择方案时，需要考虑可接受的数据丢失窗口和恢复时间。\n\n配置示例：\n    appendonly yes\n\n这是用于验证页面的测试资料。', 'zh')
        db.documents.insert_one({'source': 'arxiv', 'doc_id': 'old/123', 'title': '测试样例：只有摘要的论文',
                                 'body_text': 'An abstract, not the full paper.', 'language': 'en'})
        db.documents.insert_one({'source': 'stackexchange', 'doc_id': 'so-1', 'title': '测试样例：尚无解答的问题',
                                 'body_text': 'Question body only.', 'language': 'en'})
        mongo_sink.create_user(db, 'browser-a', 'learning-a', auth.hash_password('acceptance-password'))
        mongo_sink.create_user(db, 'browser-b', 'learning-b', auth.hash_password('acceptance-password'))

        # Same and distinct identities contend for one real Mongo profile.
        def mark(index):
            doc_id = str(index % 12)
            doc = db.documents.find_one({'source': 'official_docs', 'doc_id': doc_id})
            return store.mark_read(db, 'concurrent', 'official_docs', doc_id, True, content.content_version(doc))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(mark, range(48)))
        profile = store.profile_view(db, 'concurrent')
        assert profile['read_count'] == profile['revision'] == 12, profile
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: store.mark_read(db, 'concurrent', 'official_docs', str(i % 12), False), range(48)))
        profile = store.profile_view(db, 'concurrent')
        assert profile['read_count'] == 0 and profile['revision'] == 24, profile
        result['mongo'] = {'version': mongo.server_info()['version'], 'concurrent_marks': 48,
                           'unique_records': 12, 'concurrent_removals': 48, 'isolation': 'temporary database'}

        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        settings_file = Path('/tmp/knowpipe-chromium.json')
        settings = json.loads(settings_file.read_text()) if settings_file.exists() else {}
        # Do not disable browser web security for acceptance checks.
        browser_args = [arg for arg in settings.get('args', []) if arg not in {
            '--disable-web-security', '--allow-running-insecure-content', '--disable-site-isolation-trials'}]
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True,
                executable_path=os.environ.get('BROWSER_EXECUTABLE') or settings.get('executable'), args=browser_args)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base + '/login')
            page.locator('#username').fill('learning-a')
            page.locator('#password').fill('acceptance-password')
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(base + '/')
            page.get_by_role('link', name='进入学习工作台：学习目标与阅读记录 →').click()
            expect(page.locator('#learning-user')).to_have_text('learning-a')
            page.locator('#learning-goal').fill('理解 Redis 持久化与故障恢复')
            page.get_by_role('button', name='保存目标', exact=True).click()
            expect(page.locator('#saved-goal')).to_contain_text('理解 Redis')
            page.reload()
            expect(page.locator('#learning-goal')).to_have_value('理解 Redis 持久化与故障恢复')
            expect(page.locator('#library-items .card')).to_have_count(10)
            page.locator('#next-page').click()
            expect(page.locator('#page-label')).to_have_text('第 2 / 2 页')
            page.locator('#library-query').fill('Redis')
            page.get_by_role('button', name='查找', exact=True).click()
            expect(page.locator('#library-items .card')).to_have_count(1)
            page.get_by_role('button', name='阅读资料', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            expect(page.locator('#reading-content .transcript')).to_contain_text('RDB 保存')
            expect(page.locator('#read-count')).to_have_text('0')
            page.locator('#reading-content').get_by_role('button', name='标记已读', exact=True).click()
            expect(page.locator('#read-count')).to_have_text('1')
            page.screenshot(path=str(output / 'reading-desktop.png'))
            page.locator('#reading-close').click()
            page.get_by_role('button', name='我的已读', exact=True).click()
            expect(page.locator('#library-items .card')).to_have_count(1)
            page.get_by_role('button', name='撤销已读', exact=True).click()
            expect(page.locator('#library-items .card')).to_have_count(0)
            expect(page.locator('#read-count')).to_have_text('0')
            page.get_by_role('button', name='全部资料', exact=True).click()
            page.locator('#library-query').fill('只有摘要')
            page.get_by_role('button', name='查找', exact=True).click()
            expect(page.locator('#library-items .card')).to_have_count(1)
            page.get_by_role('button', name='阅读资料', exact=True).click()
            expect(page.locator('#reading-content .transcript')).to_contain_text('中文全文尚未就绪')
            page.get_by_role('button', name='查看原文', exact=True).click()
            expect(page.locator('#reading-content .transcript')).to_have_text('An abstract, not the full paper.')
            page.locator('#reading-close').click()

            # Open an old snapshot, then change the source before a read action.
            page.locator('#library-query').fill('Redis')
            page.get_by_role('button', name='查找', exact=True).click()
            expect(page.locator('#library-items .card')).to_have_count(1)
            page.get_by_role('button', name='阅读资料', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            content.publish_fulltext(db, 'official_docs', '0', '后台已更新的正文。', 'zh')
            page.locator('#reading-content').get_by_role('button', name='标记已读', exact=True).click()
            expect(page.locator('#learning-message')).to_contain_text('资料已更新')
            expect(page.locator('#reading-message')).to_be_visible()
            expect(page.locator('#reading-message')).to_contain_text('资料已更新')
            assert store.profile_view(db, 'browser-a')['read_count'] == 0
            page.locator('#reading-close').click()
            page.locator('#library-refresh').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('#library-items .card')).to_have_count(1)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'workspace-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(output / 'workspace-desktop.png'), full_page=True)
            page.locator('#learning-logout').click()
            page.wait_for_url(base + '/login')
            page.locator('#username').fill('learning-b')
            page.locator('#password').fill('acceptance-password')
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(base + '/')
            page.goto(base + '/learning')
            expect(page.locator('#learning-goal')).to_have_value('')
            expect(page.locator('#read-count')).to_have_text('0')
            assert not errors, errors
            browser.close()
        result['browser'] = {'desktop': '1440x1000', 'mobile': '390x844', 'horizontal_overflow': False,
                             'javascript_errors': errors, 'flows': ['login', 'home entry', 'goal persistence',
                             'pagination', 'title search', 'Chinese reading', 'explicit read and undo',
                             'abstract fallback', 'version conflict', 'logout and account isolation']}
        result['fixture_notice'] = '页面数据为隔离测试样例，不是实际语料或推荐效果证据。'
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()
        mongo.drop_database(db_name)
        mongo.close()


if __name__ == '__main__':
    main()
