#!/usr/bin/env python3
"""Read the real RSS integration result through the authenticated learning UI."""
import argparse
import json
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from knowpipe.web.app import create_app
from knowpipe.web import auth, mongo_sink


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--evidence', default='evidence/009-fulltext-podcast-learning')
    parser.add_argument('--browser-config', default='/tmp/knowpipe-chromium.json')
    args = parser.parse_args()
    out = Path(args.evidence)
    run = json.loads((out / 'rss-incremental.json').read_text())
    assert run['status'] == 'passed'
    client = MongoClient(args.mongo_uri)
    db = client[run['database']]
    uid = run['user_id']
    assert not db.users.find_one({'user_id': uid}), 'acceptance_user_already_exists'
    username, password = 'rss009-' + uuid.uuid4().hex[:12], uuid.uuid4().hex
    mongo_sink.create_user(db, uid, username, auth.hash_password(password))
    app = create_app(db=db, secret_key=uuid.uuid4().hex)
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = json.loads(Path(args.browser_config).read_text())
    report = {'scope': 'actual RSS incremental result, personal notification and Chinese reading UI',
              'page_errors': [], 'screens': []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=settings['executable'], args=settings['args'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
            base = f'http://127.0.0.1:{server.server_port}'
            page.goto(base + '/login')
            page.locator('#username').fill(username)
            page.locator('#password').fill(password)
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(base + '/')
            page.goto(base + '/learning')
            notices = page.locator('#learning-notifications .card')
            expect(notices).to_have_count(1, timeout=30000)
            expect(notices.first).to_contain_text('tmpfs')
            expect(page.locator('#learning-episodes .card')).to_have_count(1)
            for name, viewport in [('rss-personal-desktop.png', {'width': 1440, 'height': 1000}),
                                   ('rss-personal-mobile.png', {'width': 390, 'height': 844})]:
                page.set_viewport_size(viewport)
                page.locator('#learning-podcasts').scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(out / name))
                report['screens'].append(name)
            notices.first.get_by_role('button', name='阅读中文全文', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            expect(page.locator('#reading-content')).to_contain_text('未人工校对')
            text = page.locator('#reading-content .transcript').inner_text()
            assert len(text) == run['actual_translation_characters']
            expect(page.locator('#read-count')).to_have_text('0')
            page.screenshot(path=str(out / 'rss-chinese-mobile.png'))
            report['screens'].append('rss-chinese-mobile.png')
            assert not report['page_errors']
            report.update(status='passed', personal_notifications=1, episodes=1,
                          actual_chinese_characters=len(text), notification_does_not_mark_document_read=True)
            browser.close()
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:500])
        raise
    finally:
        (out / 'browser-rss.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        server.shutdown()
        db.users.delete_one({'user_id': uid})
        client.close()


if __name__ == '__main__':
    main()
