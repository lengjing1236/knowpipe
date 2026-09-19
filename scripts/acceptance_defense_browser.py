"""Verify real defense data in Chromium, including an unavailable SSE connection."""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8019')
args = parser.parse_args()
base = args.base_url.rstrip('/')
account = json.loads((ROOT / 'state/defense/demo-account.json').read_text())
settings_path = Path('/tmp/knowpipe-chromium.json')
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
executable = os.environ.get('BROWSER_EXECUTABLE', settings.get('executable'))
browser_args = [arg for arg in settings.get('args', []) if arg not in {
    '--disable-web-security', '--allow-running-insecure-content'}]
prefix = 'public' if base.startswith('https://') else 'local'
output = ROOT / 'evidence/004-podcast-insights'
errors = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=executable, args=browser_args, headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1080})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base + '/login')
    page.locator('#username').fill(account['username'])
    page.locator('#password').fill(account['password'])
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_url(base + '/')
    page.locator('#username-label').get_by_text(account['username'], exact=True).wait_for()
    page.get_by_role('heading', name='React: then & now', exact=True).wait_for(timeout=30000)
    if prefix == 'local':
        page.locator('#notification-status').get_by_text('已连接', exact=True).wait_for()
    page.screenshot(path=str(output / f'{prefix}-real-podcast-desktop.png'), full_page=True)
    page.locator('#episodes-list .card').filter(has=page.get_by_role('heading', name='React: then & now', exact=True)).get_by_role('button').click()
    page.locator('#detail-dialog[open]').wait_for()
    page.get_by_text('展开完整文字稿', exact=True).click()
    assert 'Tom Occhino' in page.locator('#detail-body .transcript').inner_text()
    assert page.locator('#detail-body .segment').count() > 20
    page.screenshot(path=str(output / f'{prefix}-real-podcast-transcript.png'))
    page.locator('#close-detail').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(output / f'{prefix}-real-podcast-mobile.png'), full_page=True)

    # The endpoint returns actual persisted notifications after one empty initial response.
    # Blocking SSE verifies that newly observed notifications arrive through polling.
    calls = [0]
    def notifications(route):
        calls[0] += 1
        if calls[0] == 1:
            route.fulfill(status=200, content_type='application/json', body='{"items":[]}')
        else:
            route.continue_()
    page.route('**/api/notifications', notifications)
    page.route('**/api/notifications/stream', lambda route: route.abort())
    page.reload()
    page.locator('#notifications-list').get_by_role('button', name='React: then & now', exact=True).wait_for(timeout=20000)
    assert calls[0] >= 2
    page.locator('#notification-status').get_by_text('定时检查中', exact=True).wait_for(timeout=10000)
    assert not errors, errors
    page.locator('#logout').click()
    page.wait_for_url(base + '/login')
    browser.close()

print(json.dumps({'base_url': base, 'real_podcast_detail': 'passed',
                  'desktop_1440': 'passed', 'mobile_390_no_overflow': 'passed',
                  'SSE_unavailable_polling_actual_persisted_notifications': 'passed',
                  'javascript_errors': errors}, ensure_ascii=False, indent=2))
