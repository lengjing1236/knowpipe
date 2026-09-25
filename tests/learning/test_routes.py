import unittest

import mongomock

from knowpipe.web.app import create_app
from knowpipe.learning import content


class LearningRoutesTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.app = create_app(db=self.db, secret_key="test-learning-secret")
        self.app.testing = True
        self.client = self.app.test_client()
        self.user_id, self.token = self.login(self.client, "alice")
        self.db.documents.insert_one({"source": "arxiv", "doc_id": "old/123", "title": "事务",
                                      "language": "en", "body_text": "An abstract"})

    def login(self, client, name):
        token = client.get("/api/auth/csrf").get_json()["csrf_token"]
        headers = {"X-CSRF-Token": token}
        registered = client.post("/api/auth/register", json={"username": name, "password": "password123"}, headers=headers)
        self.assertEqual(registered.status_code, 201, registered.get_json())
        user = registered.get_json()
        logged_in = client.post("/api/auth/login", json={"username": name, "password": "password123"}, headers=headers)
        self.assertEqual(logged_in.status_code, 200, logged_in.get_json())
        return user["user_id"], client.get("/api/auth/csrf").get_json()["csrf_token"]

    def put(self, path, data):
        if path == "read-state" and data.get("read") is True and "content_version" not in data:
            doc = self.db.documents.find_one({"source": "arxiv", "doc_id": "old/123"})
            data = dict(data, content_version=content.content_version(doc) if doc else None)
        return self.client.put("/api/learning/" + path, json=data, headers={"X-CSRF-Token": self.token})

    def test_goal_persists_is_idempotent_and_isolated(self):
        for _ in range(2):
            resp = self.put("goal", {"text": "  理解 Redis 持久化  "})
            self.assertEqual(resp.status_code, 200)
        profile = self.client.get("/api/learning/profile").get_json()
        self.assertEqual(profile["goal"]["text"], "理解 Redis 持久化")
        self.assertEqual(profile["revision"], 1)
        other = self.app.test_client()
        self.login(other, "bob")
        self.assertIsNone(other.get("/api/learning/profile").get_json()["goal"])
        self.assertEqual(self.put("goal", {"text": "attack", "user_id": "bob"}).status_code, 400)
        self.assertEqual(self.client.get("/api/learning/profile?user_id=bob").status_code, 400)

    def test_auth_csrf_and_goal_validation(self):
        anonymous = self.app.test_client()
        for path in ["profile", "documents", "document?source=arxiv&doc_id=old%2F123", "history"]:
            self.assertEqual(anonymous.get("/api/learning/" + path).status_code, 401)
        self.assertEqual(self.client.put("/api/learning/goal", json={"text": "有效目标"}).status_code, 403)
        for text in ["", " \n ", 42, [], "a" * 1001, None]:
            self.assertEqual(self.put("goal", {"text": text}).status_code, 400)
        self.assertEqual(self.client.put("/api/learning/goal", json=[], headers={"X-CSRF-Token": self.token}).status_code, 400)

    def test_only_explicit_read_action_adds_history(self):
        for _ in range(2):
            resp = self.client.get("/api/learning/document?source=arxiv&doc_id=old%2F123")
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get("/api/learning/history").get_json()["total"], 0)
        body = {"source": "arxiv", "doc_id": "old/123", "read": True}
        self.assertEqual(self.put("read-state", body).status_code, 200)
        self.assertEqual(self.put("read-state", body).status_code, 200)
        self.assertEqual(self.client.get("/api/learning/history").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/learning/profile").get_json()["revision"], 1)
        other = self.app.test_client()
        self.login(other, "bob")
        self.assertEqual(other.get("/api/learning/history").get_json()["total"], 0)
        self.db.documents.delete_many({})
        self.assertEqual(self.put("read-state", dict(body, read=False)).status_code, 200)
        self.assertEqual(self.client.get("/api/learning/history").get_json()["total"], 0)

    def test_read_validation_and_unknown_document(self):
        body = {"source": "arxiv", "doc_id": "old/123", "read": True}
        for override in [{"read": 1}, {"read": "true"}, {"source": {"$ne": None}}, {"doc_id": "\n"}, {"user_id": "other"}]:
            self.assertEqual(self.put("read-state", dict(body, **override)).status_code, 400)
        self.assertEqual(self.put("read-state", dict(body, doc_id="missing")).status_code, 404)
        self.assertEqual(self.client.put("/api/learning/read-state", json=body).status_code, 403)

    def test_pagination_and_missing_detail(self):
        for path in ["documents?limit=0", "documents?limit=51", "history?page=0", "documents?page=1.2", "documents?q=" + "a" * 101]:
            self.assertEqual(self.client.get("/api/learning/" + path).status_code, 400)
        self.assertEqual(self.client.get("/api/learning/document?source=arxiv&doc_id=missing").status_code, 404)
        response = self.client.get("/api/learning/documents?q=事务").get_json()
        self.assertEqual(response["total"], 1)
        self.assertNotIn("original_text", response["items"][0])

    def test_read_does_not_become_known_on_legacy_topic_save(self):
        self.db.documents.update_one({}, {"$set": {"mining": {"batch_id": "b1"}}})
        self.db.mining_results.insert_one({"source": "arxiv", "doc_id": "old/123", "batch_id": "b1",
                                          "topic_cluster_id": 0, "keywords": [{"term": "transactions", "weight": 0.9}]})
        self.assertEqual(self.put("read-state", {"source": "arxiv", "doc_id": "old/123", "read": True}).status_code, 200)
        resp = self.client.post("/api/profile/topics", json={"user_id": self.user_id, "known_topics": []},
                                headers={"X-CSRF-Token": self.token})
        self.assertEqual(resp.status_code, 200)
        profile = self.db.user_profiles.find_one({"user_id": self.user_id})
        self.assertEqual(profile["known_keywords"], [])

    def test_chinese_page_and_home_entry(self):
        page = self.client.get("/learning").get_data(as_text=True)
        self.assertIn('lang="zh-CN"', page)
        self.assertIn("学习目标", page)
        self.assertIn("learning.js", page)
        self.assertIn('href="/learning"', self.client.get("/").get_data(as_text=True))

    def test_marking_version_changed_after_open_returns_conflict(self):
        opened = self.client.get("/api/learning/document?source=arxiv&doc_id=old%2F123").get_json()
        content.publish_fulltext(self.db, "arxiv", "old/123", "A newer full paper", "en")
        resp = self.put("read-state", {"source": "arxiv", "doc_id": "old/123", "read": True,
                                      "content_version": opened["content_version"]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "content_changed")
        self.assertEqual(self.client.get("/api/learning/history").get_json()["total"], 0)
