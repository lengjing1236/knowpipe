"""Browser acceptance for the isolated real-Mongo fixture server (Playwright optional)."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

settings_path = Path('/tmp/knowpipe-chromium.json')
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
if os.environ.get('BROWSER_EXECUTABLE'):
    settings['executable'] = os.environ['BROWSER_EXECUTABLE']
errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path=settings.get('executable'), args=settings.get('args'), headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8018/login')
    page.locator('#username').fill('acceptance')
    page.locator('#password').fill('acceptance-password123')
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_url('http://127.0.0.1:8018/')
    page.locator('#username-label').get_by_text('acceptance', exact=True).wait_for()
    page.locator('#metric-episodes').get_by_text('1', exact=True).wait_for()
    page.locator('#notification-status').get_by_text('已连接', exact=True).wait_for()
    assert '?' not in page.url
    page.screenshot(path='evidence/003-public-web/browser-desktop.png', full_page=True)
    page.get_by_role('button', name='阅读文字稿与分析', exact=True).click()
    page.locator('#detail-dialog[open]').wait_for()
    page.get_by_text('展开完整文字稿', exact=True).click()
    assert '分布式计算' in page.locator('#detail-body .transcript').inner_text()
    assert page.locator('#detail-body .segment').count() == 2
    page.screenshot(path='evidence/004-podcast-insights/browser-transcript.png')
    page.locator('#close-detail').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='evidence/003-public-web/browser-mobile.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#logout').click()
    page.wait_for_url('http://127.0.0.1:8018/login')
    assert not errors, errors
    browser.close()
print(json.dumps({'desktop': '1440px passed', 'mobile': '390px no horizontal overflow',
                  'flows': ['login without URL user_id', 'session bootstrap', 'SSE connected', 'transcript and Spark segments', 'logout'],
                  'javascript_errors': errors}, ensure_ascii=False, indent=2))
