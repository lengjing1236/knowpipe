"""Record genuine browser interactions; shorten only measured compute waits.

Uses an existing isolated demo service. Never injects recommendation responses.
Private credentials and raw footage remain in ignored state/delivery012/.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8019')
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--output', default='evidence/012-demo-delivery')
    parser.add_argument('--video', default='submission/2026-09-25/04-备份演示视频/Knowpipe演示.mp4')
    args = parser.parse_args()
    out = ROOT / args.output
    raw = ROOT / 'state/delivery012/raw'
    raw.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    credentials = json.loads(Path(args.credentials).read_text())
    username = credentials.get('username', credentials.get('user_id'))
    password = credentials['password']
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'scope': '真实Web操作；原生中文演示子集；不作为完整万条质量通过证明',
              'goal': args.goal, 'mocked_responses': False, 'steps': [], 'page_errors': [],
              'cuts': [], 'stages': [], 'free_remote_translation_quality': 'not_tested'}
    start = None
    def stage(name, explanation):
        pending = [cut for cut in result['cuts'] if not cut.get('captioned')]
        if pending:
            explanation += '；实际等待' + '、'.join(str(cut['actual_wait_seconds']) for cut in pending) + '秒，等待已剪辑'
            for cut in pending:
                cut['captioned'] = True
        result['stages'].append({'name': name, 'caption': explanation, 'time': time.monotonic() - start})
        print(name, flush=True)
    def get(page, path):
        response = page.request.get(args.base + path)
        if not response.ok:
            raise RuntimeError(f'{path}: HTTP {response.status}')
        return response.json()
    def wait_ready(page, previous=None):
        wait_start = time.monotonic()
        deadline = wait_start + args.timeout
        observed = []
        while time.monotonic() < deadline:
            data = get(page, '/api/learning/recommendations')
            current = (data.get('status'), data.get('input_revision'))
            if not observed or observed[-1]['state'] != list(current):
                observed.append({'elapsed': round(time.monotonic() - wait_start, 2), 'state': list(current)})
            newer = previous is None or data.get('input_revision') != previous
            if newer and data.get('status') in ('ready', 'empty'):
                elapsed = time.monotonic() - wait_start
                if elapsed > 12:
                    result['cuts'].append({'start': wait_start - start + 4,
                                           'end': time.monotonic() - start - 3,
                                           'reason': '真实计算等待已剪辑', 'actual_wait_seconds': round(elapsed, 2)})
                result.setdefault('job_observations', []).append(observed)
                return data
            if data.get('status') == 'failed':
                raise RuntimeError('recommendation_failed:' + str(data.get('reason')))
            page.wait_for_timeout(1000)
        raise TimeoutError('actual_recommendation_timeout')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/tmp/knowpipe-browser/chromium',
                                   headless=True, args=['--no-sandbox', '--disable-gpu'])
        context = browser.new_context(viewport={'width': 1440, 'height': 900},
                                      record_video_dir=str(raw), record_video_size={'width': 1440, 'height': 900})
        start = time.monotonic()
        page = context.new_page()
        video = page.video
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        try:
            stage('登录系统', '真实浏览器操作 · 本机运行')
            page.goto(args.base + '/login')
            page.wait_for_timeout(1600)
            page.locator('#username').fill(username)
            page.locator('#password').fill(password)
            page.wait_for_timeout(1000)
            page.get_by_role('button', name='登录', exact=True).click()
            page.wait_for_url(args.base + '/')
            page.goto(args.base + '/learning')
            expect(page.locator('#learning-goal')).to_be_visible()
            expect(page.locator('#read-count')).to_have_text('0', timeout=15000)
            page.wait_for_timeout(1500)
            stage('设置学习目标', '输入中文问题，后台使用实际资料计算推荐')
            page.locator('#learning-goal').fill(args.goal)
            page.wait_for_timeout(2200)
            page.locator('#goal-form button[type=submit]').click()
            result['steps'].append('goal_saved_via_browser')
            page.wait_for_timeout(2000)
            first = wait_ready(page)
            if not first.get('items'):
                raise AssertionError('no_recommendation_for_demo_goal')
            result['before'] = first
            page.reload()
            expect(page.locator('#recommendation-items .card').first).to_be_visible(timeout=20000)
            page.wait_for_timeout(1500)
            stage('查看推荐与来源', '页面结果来自实际计算；资料数量和来源以页面为准')
            page.screenshot(path=str(out / '01-workspace.png'))
            result['steps'].append('real_recommendations_visible')
            page.wait_for_timeout(7000)
            card = page.locator('#recommendation-items .card').first
            card.locator('summary').first.click()
            card.scroll_into_view_if_needed()
            stage('核对推荐依据', '展开原文依据，判断这篇资料是否帮助当前目标')
            page.wait_for_timeout(9000)
            page.screenshot(path=str(out / '02-evidence.png'))
            card.get_by_role('button', name='阅读中文全文', exact=True).click()
            expect(page.locator('#reading-dialog')).to_be_visible()
            expect(page.locator('#read-count')).to_have_text('0')
            expect(page.locator('#reading-content .transcript').first).not_to_have_text('中文全文尚未就绪。你可以查看原文与当前处理状态。')
            stage('阅读中文全文', '这里展示来源提供的中文原文，无需模型翻译')
            result['read_title'] = page.locator('#reading-title').inner_text()
            page.screenshot(path=str(out / '03-reading.png'))
            page.wait_for_timeout(6000)
            page.locator('#reading-dialog').evaluate('e => e.scrollBy({top: 430, behavior: "smooth"})')
            page.wait_for_timeout(4500)
            page.locator('#reading-dialog').evaluate('e => e.scrollTo({top: 0, behavior: "smooth"})')
            page.wait_for_timeout(1000)
            stage('主动标记已读', '打开资料不会自动加入历史；只有主动标记才记录')
            page.locator('#reading-dialog').get_by_role('button', name='标记已读', exact=True).click()
            expect(page.locator('#read-count')).to_have_text('1', timeout=20000)
            result['steps'].append('explicit_read_persisted')
            page.wait_for_timeout(2500)
            page.locator('#reading-close').click()
            page.locator('#show-history').click()
            expect(page.locator('#library-items .card')).to_have_count(1)
            page.locator('#library-heading').scroll_into_view_if_needed()
            stage('查看个人阅读记录', '已读记录保留具体资料版本；已读不等于已经掌握')
            page.wait_for_timeout(7000)
            page.screenshot(path=str(out / '04-history.png'))
            page.locator('#recommendation-heading').scroll_into_view_if_needed()
            second = wait_ready(page, first.get('input_revision'))
            result['after'] = second
            result['profile'] = get(page, '/api/learning/profile')
            result['history'] = get(page, '/api/learning/history')
            page.reload()
            expect(page.locator('#read-count')).to_have_text('1', timeout=20000)
            page.wait_for_timeout(2500)
            stage('按阅读历史重新计算', '重算使用新的已读记录；资料不足时允许减少推荐')
            page.screenshot(path=str(out / '05-recomputed.png'))
            result['steps'].append('new_revision_recomputed')
            page.wait_for_timeout(9000)
            page.locator('#library-heading').scroll_into_view_if_needed()
            stage('按来源查找资料', '统一全文资料库，支持来源筛选、搜索和阅读')
            page.locator('#library-source').select_option('django_docs')
            with page.expect_response(lambda r: '/api/learning/documents?' in r.url and 'source=django_docs' in r.url) as response:
                page.locator('#library-search button[type=submit]').click()
            filtered = response.value.json()
            assert filtered['items'] and all(item['source'] == 'django_docs' for item in filtered['items'])
            expect(page.locator('#library-items .card')).to_have_count(len(filtered['items']))
            expect(page.locator('#library-items .card').first.locator('.card-meta')).to_contain_text('Django 官方文档')
            page.wait_for_timeout(6500)
            page.screenshot(path=str(out / '06-library.png'))
            stage('RSS订阅入口', '可管理订阅；本段不把订阅入口当作音频到通知全流程验收')
            page.locator('#learning-podcasts').scroll_into_view_if_needed()
            page.wait_for_timeout(6500)
            page.screenshot(path=str(out / '07-rss.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('window.scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(out / '08-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.evaluate('window.scrollTo(0,0)')
            stage('演示完成', '目标 → 实际推荐 → 中文阅读 → 已读记录 → 重新推荐')
            page.wait_for_timeout(7000)
            result['steps'].append('source_filter_and_mobile_verified')
            result['status'] = 'browser_passed_video_pending'
        except Exception as exc:
            result['status'] = 'failed'
            result['error_type'] = type(exc).__name__
            result['error'] = str(exc)[:500]
            page.screenshot(path=str(out / 'failure.png'), full_page=True)
            raise
        finally:
            result['raw_seconds'] = round(time.monotonic() - start, 2)
            context.close()
            result['raw_video'] = str(video.path().relative_to(ROOT))
            browser.close()
            dump(out / 'browser.json', result)
    # Only remove captured idle waits. Every retained frame remains actual UI.
    source = ROOT / result['raw_video']
    intervals, cursor = [], 0.0
    for cut in sorted(result['cuts'], key=lambda x: x['start']):
        if cut['start'] > cursor:
            intervals.append((cursor, cut['start']))
        cursor = max(cursor, cut['end'])
    intervals.append((cursor, result['raw_seconds']))
    video_out = ROOT / args.video
    video_out.parent.mkdir(parents=True, exist_ok=True)
    def edited_time(value):
        return max(0, value - sum(max(0, min(value, c['end']) - c['start']) for c in result['cuts']))
    def timestamp(seconds):
        ms=int(seconds*1000); return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    subtitles=[]
    for i, item in enumerate(result['stages']):
        end = result['stages'][i+1]['time'] if i+1 < len(result['stages']) else result['raw_seconds']
        a,b=edited_time(item['time']),edited_time(end)
        if b>a:
            subtitles.append(f'{len(subtitles)+1}\n{timestamp(a)} --> {timestamp(b)}\n{item["name"]}｜{item["caption"]}\n')
    srt=raw/'captions.srt';srt.write_text('\n'.join(subtitles),encoding='utf-8')
    filters=[]
    for i,(a,b) in enumerate(intervals):
        filters.append(f'[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]')
    labels=''.join(f'[v{i}]' for i in range(len(intervals)))
    filters.append(labels+f'concat=n={len(intervals)}:v=1:a=0,pad=1440:980:0:0:color=0x172d29,'+
                   f"subtitles={srt}:force_style='FontName=Droid Sans Fallback,FontSize=18,PrimaryColour=&HFFFFFF,Outline=0,MarginV=12'[out]")
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source),'-filter_complex',';'.join(filters),
                    '-map','[out]','-r','25','-c:v','libx264','-threads','2','-preset','fast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(video_out)],check=True)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration,size','-of','json',str(video_out)]))
    if float(probe['format']['duration']) > 300:
        raise AssertionError('video_exceeds_300_seconds')
    subprocess.run(['ffmpeg','-v','error','-i',str(video_out),'-f','null','-'],check=True)
    result['video']={'path':str(video_out.relative_to(ROOT)), **probe['format'],
                     'sha256':hashlib.sha256(video_out.read_bytes()).hexdigest(), 'has_chinese_captions':True,
                     'edits':'仅删除browser.json列出的实际等待；未更改返回结果', 'audio':'无旁白，含中文说明字幕'}
    result['status'] = 'passed'
    dump(out/'browser.json',result)
    (video_out.parent/'演示说明.md').write_text('# 备份演示视频\n\n'+
        '真实浏览器操作，中文字幕，无旁白。演示使用原生中文官方资料子集，不冒充万条实时推荐。\n\n'+
        f'时长：{float(probe["format"]["duration"]):.1f}秒。'+
        ('计算等待有剪辑，具体区间及实际等待时间见evidence/012-demo-delivery/browser.json。' if result['cuts'] else '本次没有剪除计算等待。')+
        '\n\n覆盖登录、中文目标、推荐依据、全文阅读、显式已读、重新推荐和资料筛选。RSS仅展示管理入口，自动翻译未作真实远程质量验收。\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'video':str(video_out),'duration':probe['format']['duration']},ensure_ascii=False))


if __name__=='__main__':
    main()
