#!/usr/bin/env python3
"""Browser acceptance against retained real scale results, not synthetic ranking."""
import argparse
import json
import sys
import threading
import uuid
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server,WSGIRequestHandler
from knowpipe.web.app import create_app
from knowpipe.web import auth,mongo_sink
from knowpipe.learning import store as learning
from knowpipe.recommendations import queue
from knowpipe.recommendations.worker import RecommendationWorker
from knowpipe.podcasts import store as podcasts
from knowpipe.podcasts.learning import publish_recommendations

class QuietHandler(WSGIRequestHandler):
    def log_request(self,*_):pass

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27018')
    parser.add_argument('--evidence',default='evidence/009-fulltext-podcast-learning')
    args=parser.parse_args();out=Path(args.evidence)
    scale=json.loads((out/'scale.json').read_text())
    assert scale['status']=='passed'
    client=MongoClient(args.mongo_uri);db=client[scale['database']]
    uid='browser009-'+uuid.uuid4().hex[:12]
    password=uuid.uuid4().hex
    mongo_sink.create_user(db,uid,uid,auth.hash_password(password))
    learning.save_goal(db,uid,scale['goal'])
    profile=db.user_profiles.find_one({'user_id':uid})
    snapshot=scale['index']
    queue.ensure_indexes(db)
    db.recommendation_runtime.update_one({'_id':'worker'},{'$set':{'corpus':snapshot,'generation':1,'lease_expires_at':datetime.utcnow()+timedelta(hours=1)}})
    jid=queue.schedule(db,profile,db.recommendation_runtime.find_one({'_id':'worker'}))
    # These scores/evidence were computed in the real scale Spark run. This script
    # only prepares their Chinese content and exercises the Web presentation.
    recommendation=scale['recommendation']
    worker=RecommendationWorker(db,'state/feature009/scale-index')
    worker.prepare_chinese(recommendation)
    db.recommendation_jobs.update_one({'_id':jid},{'$set':{'status':'ready','result':recommendation,'finished_at':datetime.utcnow()}})
    app=create_app(db=db,secret_key=uuid.uuid4().hex)
    server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    settings=json.loads(Path('/tmp/knowpipe-chromium.json').read_text())
    report={'scope':'real scale result Web presentation, actual selected-document translation, RSS form and account isolation',
            'source_scale':'scale.json','screens':[],'page_errors':[]}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=settings['executable'],args=settings['args'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login');page.locator('#username').fill(uid);page.locator('#password').fill(password)
            page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url(base+'/')
            page.goto(base+'/learning')
            expect(page.locator('#recommendation-status')).to_contain_text('找到',timeout=30000)
            expect(page.locator('#recommendation-context')).to_contain_text(str(snapshot['document_count']))
            page.locator('#recommendation-items .card').first.locator('summary').click()
            page.screenshot(path=str(out/'learning-desktop.png'));report['screens'].append('learning-desktop.png')
            ready=page.locator('#recommendation-items').get_by_role('button',name='阅读中文全文',exact=True)
            assert ready.count()>0,'no_actual_chinese_translation'
            ready.first.click();expect(page.locator('#reading-dialog')).to_be_visible()
            text=page.locator('#reading-content .transcript').inner_text()
            assert any('\u4e00'<=c<='\u9fff' for c in text)
            expect(page.locator('#read-count')).to_have_text('0')
            page.screenshot(path=str(out/'chinese-reading.png'))
            report['screens'].append('chinese-reading.png')
            page.locator('#reading-close').click()
            page.locator('#podcast-feed-url').fill('https://feeds.transistor.fm/aws-morning-brief')
            page.get_by_role('button',name='添加订阅',exact=True).click()
            expect(page.locator('#podcast-status')).to_contain_text('已订阅 1',timeout=30000)
            page.locator('#learning-podcasts').scroll_into_view_if_needed()
            page.screenshot(path=str(out/'podcasts-desktop.png'))
            report['screens'].append('podcasts-desktop.png')
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#learning-podcasts').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(out/'podcasts-mobile.png'))
            report['screens'].append('podcasts-mobile.png')
            page.get_by_role('button',name='取消订阅',exact=True).click()
            expect(page.locator('#podcast-status')).to_contain_text('已订阅 0')
            # Goal change must immediately remove previous recommendation cards.
            page.locator('#learning-goal').fill('Redis 持久化恢复')
            page.get_by_role('button',name='保存目标',exact=True).click()
            expect(page.locator('#saved-goal')).to_contain_text('Redis')
            expect(page.locator('#recommendation-items .card')).to_have_count(0)
            assert not report['page_errors'],report['page_errors']
            report.update(status='passed',mobile_overflow=False,chinese_reading=True,
                opening_does_not_mark_read=True,subscription_add_remove=True,old_goal_cards_removed=True)
            browser.close()
    finally:
        (out/'browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        server.shutdown();worker.close()
        db.users.delete_one({'user_id':uid});db.user_profiles.delete_one({'user_id':uid})
        db.recommendation_jobs.delete_many({'user_id':uid});db.podcast_subscriptions.delete_many({'user_id':uid})
        db.podcast_quotas.delete_many({'_id':uid})
        db.recommendation_runtime.update_one({'_id':'worker'}, {'$set': {'lease_expires_at':datetime(1970,1,1)}})
        client.close()

if __name__=='__main__':main()
