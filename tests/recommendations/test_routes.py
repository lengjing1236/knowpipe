import unittest
import mongomock
from knowpipe.web.app import create_app
from knowpipe.recommendations import queue


class RecommendationRoutesTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.app = create_app(db=self.db, secret_key='recommendations-test')
        self.app.testing = True
        self.client = self.app.test_client()
        token = self.client.get('/api/auth/csrf').get_json()['csrf_token']
        body = {'username': 'alice', 'password': 'password123'}
        self.client.post('/api/auth/register', json=body, headers={'X-CSRF-Token': token})
        self.client.post('/api/auth/login', json=body, headers={'X-CSRF-Token': token})
        self.headers = {'X-CSRF-Token': self.client.get('/api/auth/csrf').get_json()['csrf_token']}

    def test_session_csrf_identity_and_shape(self):
        path = '/api/learning/recommendations'
        self.assertEqual(self.app.test_client().get(path).status_code, 401)
        self.assertEqual(self.client.post(path, json={}).status_code, 403)
        for body in ({'user_id': 'another'}, {'goal': 'override'}, []):
            self.assertEqual(self.client.post(path, json=body, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get(path+'?user_id=another').status_code, 400)
        response = self.client.post(path, json={}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['status'], 'needs_goal')

    def test_durable_intent_without_spark_or_worker(self):
        self.client.put('/api/learning/goal', json={'text': '数据库事务'}, headers=self.headers)
        response = self.client.post('/api/learning/recommendations', json={}, headers=self.headers)
        self.assertEqual(response.get_json()['status'], 'waiting_for_worker')
        self.assertEqual(response.get_json()['items'], [])
        self.assertIsNotNone(self.db.user_profiles.find_one({}).get('recommendation_requested_at'))
        # Web neither starts Spark nor creates fake synchronous results.
        self.assertEqual(self.db.recommendation_jobs.count_documents({}), 0)
        owner = queue.acquire_worker(self.db)
        runtime = queue.publish_corpus(self.db, owner, {'corpus_id': 'empty', 'document_count': 0})
        queue.schedule(self.db, self.db.user_profiles.find_one({}), runtime)
        self.assertEqual(self.client.get('/api/learning/recommendations').get_json()['status'], 'queued')
