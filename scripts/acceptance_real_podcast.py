"""Real public RSS -> resident worker/Spark -> Mongo -> authenticated HTTP acceptance.

Start scripts/defense_demo.py first. Credentials stay under ignored state/defense/.
No generated transcripts, injected fetchers, or database substitutes are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time

import requests
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from knowpipe.podcasts.feeds import parse_feed
from knowpipe.podcasts.network import fetch_bytes

STATE = ROOT / 'state/defense'
FEED = 'https://changelog.com/jsparty/feed'


def login(base, username, password, register=False):
    session = requests.Session()
    csrf = session.get(base + '/api/auth/csrf', timeout=20)
    csrf.raise_for_status()
    headers = {'X-CSRF-Token': csrf.json()['csrf_token']}
    body = {'username': username, 'password': password}
    if register:
        response = session.post(base + '/api/auth/register', json=body, headers=headers, timeout=20)
        assert response.status_code in (201, 409), response.text
    response = session.post(base + '/api/auth/login', json=body, headers=headers, timeout=20)
    response.raise_for_status()
    csrf = session.get(base + '/api/auth/csrf', timeout=20).json()['csrf_token']
    return session, {'X-CSRF-Token': csrf}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8019')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    credentials_path = STATE / 'demo-account.json'
    if not credentials_path.exists():
        credentials_path.write_text(json.dumps({'username': 'defense', 'password': secrets.token_urlsafe(18)}, indent=2))
        credentials_path.chmod(0o600)
    credentials = json.loads(credentials_path.read_text())
    session, headers = login(base, **credentials, register=True)
    identity = session.get(base + '/api/auth/me', timeout=20).json()
    response = session.post(base + '/api/podcasts/subscriptions', json={'url': FEED}, headers=headers, timeout=20)
    assert response.status_code == 201, response.text
    feed_id = response.json()['feed_id']
    raw, _ = fetch_bytes(FEED)
    title, source_items = parse_feed(raw, FEED)
    expected_ready = {item['episode_id'] for item in source_items if item['transcript_url']}
    assert expected_ready, 'Publisher has no supported transcripts in the latest three episodes'
    print(f'Live RSS: {title}, {len(raw)} bytes, {len(expected_ready)} publisher transcripts', flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        response = session.get(base + '/api/podcasts/episodes', timeout=20)
        response.raise_for_status()
        episodes = response.json()['items']
        if expected_ready <= {item['episode_id'] for item in episodes if item['status'] == 'ready'}:
            break
        print('Worker states: ' + str([(item['title'], item['status']) for item in episodes]), flush=True)
        time.sleep(5)
    else:
        raise RuntimeError('Real worker did not complete; inspect state/defense/worker.log')
    details = []
    for episode_id in sorted(expected_ready):
        detail = session.get(base + '/api/podcasts/episodes/' + episode_id, timeout=20).json()
        assert detail['transcript_origin'] == 'publisher'
        assert detail['analysis']['spark_application_id']
        assert detail['analysis']['segment_count'] > 1
        assert detail['analysis']['topic_mode'] == 'kmeans'
        details.append({key: detail[key] for key in ('episode_id', 'title', 'source_url', 'transcript_url', 'batch_id')})
        details[-1].update(transcript_characters=len(detail['transcript']),
                          transcript_sha256=hashlib.sha256(detail['transcript'].encode()).hexdigest(),
                          segment_count=detail['analysis']['segment_count'],
                          spark_application_id=detail['analysis']['spark_application_id'],
                          spark_master=detail['analysis']['spark_master'])
    # Force a fresh poll through the resident real worker, then verify idempotency.
    manifest = json.loads((STATE / 'services.json').read_text())
    with MongoClient(manifest['mongo_uri'], tz_aware=True) as client:
        db = client[manifest['database']]
        query = {'user_id': identity['user_id'], 'episode_id': {'$in': list(expected_ready)}}
        deadline = time.monotonic() + 30
        while db.notifications.count_documents(query) != len(expected_ready):
            if time.monotonic() >= deadline:
                raise RuntimeError('Completion notifications were not published')
            time.sleep(1)
        before = {item['episode_id']: item['batch_id'] for item in details}
        repoll_at = datetime.now(timezone.utc)
        db.podcast_feeds.update_one({'feed_id': feed_id}, {'$set': {'next_poll_at': repoll_at}})
        deadline = time.monotonic() + 60
        while True:
            feed = db.podcast_feeds.find_one({'feed_id': feed_id})
            if feed['last_checked_at'] >= repoll_at:
                assert feed['last_error'] is None
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Second real RSS poll did not complete')
            time.sleep(1)
        assert db.notifications.count_documents(query) == len(expected_ready)
        for episode_id, batch_id in before.items():
            assert db.podcast_episodes.count_documents({'episode_id': episode_id}) == 1
            assert db.podcast_episodes.find_one({'episode_id': episode_id})['batch_id'] == batch_id
        episode_count = db.podcast_episodes.count_documents({'feed_id': feed_id})
        notification_count = db.notifications.count_documents(query)
    other, _ = login(base, 'isolation-' + secrets.token_hex(4), secrets.token_urlsafe(18), register=True)
    assert other.get(base + '/api/podcasts/episodes/' + details[0]['episode_id'], timeout=20).status_code == 404
    assert other.get(base + '/api/podcasts/episodes', timeout=20).json()['items'] == []
    other.close()
    assert requests.get(base + '/api/notifications', timeout=20).status_code == 401
    notifications = session.get(base + '/api/notifications', timeout=20).json()['items']
    assert expected_ready <= {item['episode_id'] for item in notifications}
    assert session.get(base + '/health/ready', timeout=20).status_code == 200
    if base.startswith('https://'):
        assert all(cookie.secure for cookie in session.cookies if cookie.name == 'session')
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'base_url': base,
              'feed_url': FEED, 'feed_title': title, 'feed_bytes': len(raw),
              'feed_sha256': hashlib.sha256(raw).hexdigest(), 'episodes': details,
              'total_stored_episodes': episode_count, 'target_notifications': notification_count,
              'checks': ['real publisher RSS and HTML transcripts', 'real MongoDB and Spark local[2]',
                         'HTTP register/login/CSRF/detail/notifications', 'second public RSS poll: no duplicate target episodes or batches',
                         'second account cannot read target episodes', 'anonymous notifications denied'],
              'scope': 'Historical public episodes; no scale, recommendation quality, or new-release latency claim'}
    output = ROOT / 'evidence/004-podcast-insights' / ('real-public-https.json' if base.startswith('https://') else 'real-programming-podcast.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
