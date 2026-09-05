"""knowpipe.web.auth 的测试：密码哈希校验、@login_required 拒绝、越权校验。"""
import unittest

import mongomock

from knowpipe.web import auth, mongo_sink
from knowpipe.web.app import create_app


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_web_test"]


class TestPasswordHashing(unittest.TestCase):
    def test_verify_password_correct(self):
        password_hash = auth.hash_password("correct-password")
        self.assertTrue(auth.verify_password("correct-password", password_hash))

    def test_verify_password_incorrect(self):
        password_hash = auth.hash_password("correct-password")
        self.assertFalse(auth.verify_password("wrong-password", password_hash))


class TestLoginRequired(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.app = create_app(db=self.db)
        self.client = self.app.test_client()
        mongo_sink.create_user(self.db, "u1", "alice", auth.hash_password("password123"))

    def test_recommendations_without_session_returns_401(self):
        resp = self.client.get("/api/recommendations?user_id=u1&limit=10")
        self.assertEqual(resp.status_code, 401)

    def test_recommendations_with_valid_session_passes_auth(self):
        self.client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        resp = self.client.get("/api/recommendations?user_id=u1&limit=10")
        self.assertEqual(resp.status_code, 200)


class TestOwnershipCheck(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.app = create_app(db=self.db)
        self.client = self.app.test_client()
        mongo_sink.create_user(self.db, "u1", "alice", auth.hash_password("password123"))
        mongo_sink.create_user(self.db, "u2", "bob", auth.hash_password("password123"))

    def test_requesting_other_users_data_returns_403(self):
        self.client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        resp = self.client.get("/api/recommendations?user_id=u2&limit=10")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
