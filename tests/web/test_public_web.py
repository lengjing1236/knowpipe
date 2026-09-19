"""Feature 003: externally observable security and browser bootstrap contracts."""
import os
from datetime import datetime
import unittest
from unittest.mock import patch

import mongomock

from knowpipe.web.app import create_app


class PublicWebTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().test
        self.app = create_app(db=self.db, secret_key="t" * 40)
        self.client = self.app.test_client()
        self.token = self.client.get('/api/auth/csrf').get_json()['csrf_token']

    def post(self, path, data):
        return self.client.post(path, json=data, headers={'X-CSRF-Token': self.token})

    def test_csrf_required_and_valid_token_allows_register(self):
        data = {'username': 'alice', 'password': 'password123'}
        self.assertEqual(self.client.post('/api/auth/register', json=data).status_code, 403)
        self.assertEqual(self.post('/api/auth/register', data).status_code, 201)

    def test_session_bootstrap_no_url_user_id(self):
        data = {'username': 'alice', 'password': 'password123'}
        self.post('/api/auth/register', data)
        self.post('/api/auth/login', data)
        me = self.client.get('/api/auth/me')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json['username'], 'alice')
        self.assertEqual(me.json['known_topics'], [])
        self.assertIn('no-store', me.headers['Cache-Control'])

    def test_untrusted_json_and_query_operators_rejected(self):
        for data in [[], ['x'], {'username': {'$ne': None}, 'password': 'password123'},
                     {'username': 'alice', 'password': ['abc']},
                     {'username': 'a' * 65, 'password': 'password123'}]:
            for path in ['/api/auth/register', '/api/auth/login']:
                with self.subTest(data=data, path=path):
                    self.assertEqual(self.post(path, data).status_code, 400)
        self.assertEqual(self.db.users.count_documents({}), 0)

    def test_production_rejects_missing_secret_and_insecure_origin(self):
        with patch.dict(os.environ, {'APP_ENV': 'production', 'SECRET_KEY': '', 'PUBLIC_ORIGIN': 'https://example.com'}):
            with self.assertRaises(ValueError):
                create_app(db=self.db)
        with patch.dict(os.environ, {'APP_ENV': 'production', 'SECRET_KEY': 'x' * 40, 'PUBLIC_ORIGIN': 'http://example.com'}):
            with self.assertRaises(ValueError):
                create_app(db=self.db)

    def test_production_cookie_and_shared_rate_limit(self):
        settings = {'APP_ENV': 'production', 'SECRET_KEY': 'x' * 40, 'PUBLIC_ORIGIN': 'https://example.com'}
        with patch.dict(os.environ, settings):
            apps = [create_app(db=self.db, config={'AUTH_RATE_LIMIT': 2}) for _ in range(2)]
        clients = [app.test_client() for app in apps]
        for client in clients:
            token_resp = client.get('/api/auth/csrf', base_url='https://example.com')
            self.assertIn('Secure', token_resp.headers['Set-Cookie'])
            token = token_resp.json['csrf_token']
            resp = client.post('/api/auth/login', base_url='https://example.com',
                               json={'username': 'alice', 'password': 'password123'},
                               headers={'X-CSRF-Token': token})
            self.assertEqual(resp.status_code, 401)
        resp = clients[1].post('/api/auth/login', base_url='https://example.com',
                              json={'username': 'alice', 'password': 'password123'},
                              headers={'X-CSRF-Token': token})
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Retry-After', resp.headers)

    def test_health_and_stats_do_not_expose_secrets(self):
        self.db.batches.insert_one({'batch_id': 'b1', 'status': 'failed', 'error_message': 'secret URI', 'started_at': datetime(2026, 9, 16)})
        self.assertEqual(self.client.get('/health/live').status_code, 200)
        self.assertEqual(self.client.get('/health/ready').status_code, 200)
        resp = self.client.get('/api/stats')
        self.assertEqual(resp.json['documents'], 0)
        self.assertTrue(resp.json['batches'][0]['started_at'].endswith('+00:00'))
        self.assertNotIn('secret URI', resp.get_data(as_text=True))
        self.assertIn("default-src 'self'", resp.headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
