"""Real HTTP contract smoke against the isolated Gunicorn acceptance server."""
import json
from pathlib import Path
import requests

base = 'http://127.0.0.1:8018'
session = requests.Session()

def token():
    result = session.get(base + '/api/auth/csrf', timeout=10)
    result.raise_for_status()
    return result.json()['csrf_token']

assert session.post(base + '/api/auth/login', json={'username': 'acceptance', 'password': 'acceptance-password123'}, timeout=10).status_code == 403
result = session.post(base + '/api/auth/login', json={'username': 'acceptance', 'password': 'acceptance-password123'}, headers={'X-CSRF-Token': token()}, timeout=10)
assert result.status_code == 200, result.text
me = session.get(base + '/api/auth/me', timeout=10).json()
assert me['user_id'] == 'acceptance-user'
manifest = json.loads(Path('/tmp/knowpipe-acceptance.json').read_text())
episode = session.get(base + '/api/podcasts/episodes/' + manifest['episode_id'], timeout=10).json()
assert episode['status'] == 'ready' and episode['analysis']['spark_application_id']
notifications = session.get(base + '/api/notifications', timeout=10).json()['items']
assert len(notifications) == 1
with session.get(base + '/api/notifications/stream', stream=True, timeout=10) as response:
    event = []
    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
        event.append(line)
        if line.startswith('data: '):
            payload = json.loads(line[6:])
            assert payload['episode_id'] == manifest['episode_id']
            assert 'user_id' not in payload
            break
assert any(line == 'event: notification' for line in event)
assert requests.get(base + '/api/notifications', timeout=10).status_code == 401
assert session.get(base + '/health/ready', timeout=10).status_code == 200
assert session.get(base + '/', timeout=10).status_code == 200
print(json.dumps({'server': 'Gunicorn, 2 processes, real HTTP', 'database': manifest['database'],
                  'login_csrf_identity': 'passed', 'transcript_analysis_detail': 'passed',
                  'authenticated_sse': 'passed', 'notification_count': len(notifications),
                  'data_kind': manifest['data_kind']}, ensure_ascii=False, indent=2))
