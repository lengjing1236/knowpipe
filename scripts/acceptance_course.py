"""Real Mongo + local HTTP/browser acceptance for the course linking feature."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from werkzeug.serving import make_server
from knowpipe.web.app import create_app
from knowpipe.web import auth, mongo_sink
from knowpipe.podcasts import store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri',default='mongodb://127.0.0.1:27017')
    parser.add_argument('--mongo-db',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    client=MongoClient(args.mongo_uri);db=client[args.mongo_db]
    pointer=db.linking_state.find_one({'_id':'current'})
    assert pointer, 'Run the linking job first'
    link=db.cross_source_links.find_one({'run_id':pointer['run_id']})
    assert link, 'Real acceptance requires at least one actual link'
    episode=db.podcast_episodes.find_one({'episode_id':link['episode_id']})
    app=create_app(db=db,secret_key=secrets.token_hex(32),config={'SSE_DURATION_SECONDS':0})
    uid=uuid.uuid4().hex;name='course-check-'+uid[:8];password=secrets.token_urlsafe(24)
    mongo_sink.create_user(db,uid,name,auth.hash_password(password))
    http=app.test_client()
    with http.session_transaction() as session: session['user_id']=uid
    path='/api/podcasts/episodes/'+episode['episode_id']
    assert http.get(path).status_code==404
    db.podcast_subscriptions.insert_one({'user_id':uid,'feed_id':episode['feed_id']})
    response=http.get(path)
    assert response.status_code==200 and response.json['cross_source']['items']
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    errors=[];args.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            settings_path=Path('/tmp/knowpipe-chromium.json')
            settings=json.loads(settings_path.read_text()) if settings_path.exists() else {}
            executable=os.environ.get('BROWSER_EXECUTABLE') or settings.get('executable')
            browser=p.chromium.launch(headless=True,executable_path=executable,args=settings.get('args'))
            page=browser.new_page(viewport={'width':1440,'height':1080})
            page.on('pageerror',lambda error:errors.append(str(error)))
            base=f'http://127.0.0.1:{server.server_port}'
            page.goto(base+'/login')
            page.locator('#username').fill(name);page.locator('#password').fill(password)
            page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url(base+'/')
            page.get_by_role('button',name='阅读文字稿与分析',exact=True).first.wait_for()
            # Open the actual episode that has published links.
            page.evaluate('(id)=>showEpisode(id)',episode['episode_id'])
            page.locator('#detail-dialog[open]').wait_for()
            page.get_by_role('button',name='阅读文献',exact=True).first.wait_for()
            page.screenshot(path=str(args.output.parent/'linking-desktop.png'))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(args.output.parent/'linking-mobile.png'))
            page.get_by_role('button',name='阅读文献',exact=True).first.click()
            page.locator('#detail-body .transcript').wait_for()
            assert page.locator('#detail-body .transcript').inner_text().strip()
            assert not errors,errors
            browser.close()
        report={'run_id':pointer['run_id'],'episode_id':episode['episode_id'],
                'document_count':db.documents.count_documents({}),
                'visible_links':len(response.json['cross_source']['items']),
                'checks':['real Mongo linked API','unsubscribed 404','HTTP login','desktop related reading',
                          '390px mobile no overflow','open document body'], 'javascript_errors':errors}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    finally:
        server.shutdown();thread.join(timeout=3)
        for collection in ['users','user_profiles','user_knowledge','podcast_subscriptions']:
            db[collection].delete_many({'user_id':uid})
        client.close()


if __name__=='__main__':main()
