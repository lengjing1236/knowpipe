"""/api/* 路由的契约测试：app.test_client()，数据库用 mongomock 注入替身。"""
import unittest
from datetime import datetime, timezone

import mongomock

from knowpipe.web.app import create_app


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_web_test"]


def _seed_document(db, source, doc_id, keywords, topic_cluster_id, batch_id="b1",
                    similar_doc_ids=None, reliable=True, created_at=None):
    created_at = created_at or datetime.now(timezone.utc)
    db.documents.insert_one({
        "source": source, "doc_id": doc_id, "title": f"title-{doc_id}",
        "source_url": f"https://example.com/{doc_id}", "created_at": created_at,
        "mining": {"batch_id": batch_id, "topic_cluster_id": topic_cluster_id,
                    "keyword_count": len(keywords)},
    })
    db.mining_results.insert_one({
        "doc_id": doc_id, "source": source, "batch_id": batch_id,
        "keywords": [{"term": k, "weight": w} for k, w in keywords],
        "topic_cluster_id": topic_cluster_id,
        "similar_doc_ids": similar_doc_ids or [],
        "reliable": reliable,
    })


class RoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.app = create_app(db=self.db)
        self.client = self.app.test_client()

    def register_and_login(self, username="demo", password="demo-pass-1234"):
        resp = self.client.post("/api/auth/register", json={"username": username, "password": password})
        user_id = resp.get_json()["user_id"]
        self.client.post("/api/auth/login", json={"username": username, "password": password})
        return user_id


class TestAuthContract(RoutesTestCase):
    def test_register_success(self):
        resp = self.client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["username"], "alice")

    def test_register_duplicate_username_returns_409(self):
        self.client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
        resp = self.client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
        self.assertEqual(resp.status_code, 409)

    def test_register_missing_fields_returns_400(self):
        resp = self.client.post("/api/auth/register", json={"username": "alice"})
        self.assertEqual(resp.status_code, 400)

    def test_login_success_sets_session(self):
        self.client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
        resp = self.client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
        self.assertEqual(resp.status_code, 200)

    def test_login_wrong_password_returns_401(self):
        self.client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
        resp = self.client.post("/api/auth/login", json={"username": "alice", "password": "wrong-pass"})
        self.assertEqual(resp.status_code, 401)


class TestPublicReadContract(RoutesTestCase):
    def test_topics_sorted_by_document_count_desc(self):
        _seed_document(self.db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        _seed_document(self.db, "arxiv", "a2", [("spark", 0.8)], topic_cluster_id=0)
        _seed_document(self.db, "stackexchange", "s1", [("docker", 0.7)], topic_cluster_id=1)

        resp = self.client.get("/api/topics")
        self.assertEqual(resp.status_code, 200)
        topics = resp.get_json()["topics"]
        self.assertEqual(topics[0]["topic_cluster_id"], 0)
        self.assertEqual(topics[0]["document_count"], 2)

    def test_document_detail_found(self):
        _seed_document(self.db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        resp = self.client.get("/api/documents/arxiv/a1")
        self.assertEqual(resp.status_code, 200)

    def test_document_detail_not_found_returns_404(self):
        resp = self.client.get("/api/documents/arxiv/missing")
        self.assertEqual(resp.status_code, 404)


class TestRecommendationsContract(RoutesTestCase):
    def setUp(self):
        super().setUp()
        _seed_document(self.db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        _seed_document(self.db, "stackexchange", "s1", [("docker", 0.7)], topic_cluster_id=1)
        self.user_id = self.register_and_login()

    def test_unauthenticated_returns_401(self):
        client = self.app.test_client()
        resp = client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        self.assertEqual(resp.status_code, 401)

    def test_other_users_id_returns_403(self):
        resp = self.client.get("/api/recommendations?user_id=someone-else&limit=10")
        self.assertEqual(resp.status_code, 403)

    def test_invalid_limit_returns_400(self):
        resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=-1")
        self.assertEqual(resp.status_code, 400)

    def test_valid_request_returns_items_with_status(self):
        resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()["items"]
        self.assertTrue(len(items) > 0)
        for item in items:
            self.assertIn(item["status"], ("known", "refine", "new", "possible_conflict"))

    def test_new_user_without_profile_gets_all_new(self):
        resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        items = resp.get_json()["items"]
        self.assertTrue(all(item["status"] == "new" for item in items))

    def test_baseline_mode_excludes_personalized_fields(self):
        resp = self.client.get(
            f"/api/recommendations?user_id={self.user_id}&limit=10&mode=baseline")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["mode"], "baseline")
        for item in body["items"]:
            self.assertNotIn("status", item)
            self.assertNotIn("matched_keywords", item)
            self.assertNotIn("matched_topic_cluster_id", item)


class TestProfileTopicsContract(RoutesTestCase):
    def setUp(self):
        super().setUp()
        _seed_document(self.db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        self.user_id = self.register_and_login()

    def test_unauthenticated_returns_401(self):
        client = self.app.test_client()
        resp = client.post("/api/profile/topics", json={"user_id": self.user_id, "known_topics": ["0"]})
        self.assertEqual(resp.status_code, 401)

    def test_other_users_id_returns_403(self):
        resp = self.client.post("/api/profile/topics",
                                json={"user_id": "someone-else", "known_topics": ["0"]})
        self.assertEqual(resp.status_code, 403)

    def test_invalid_topic_id_returns_400(self):
        resp = self.client.post("/api/profile/topics",
                                json={"user_id": self.user_id, "known_topics": ["999"]})
        self.assertEqual(resp.status_code, 400)

    def test_success_returns_updated_known_topics(self):
        resp = self.client.post("/api/profile/topics",
                                json={"user_id": self.user_id, "known_topics": ["0"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["known_topics"], ["0"])

        rec_resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        items = rec_resp.get_json()["items"]
        spark_item = next(i for i in items if i["knowledge_id"] == "kw:spark")
        self.assertIn(spark_item["status"], ("known", "refine"))


class TestProfileFeedbackContract(RoutesTestCase):
    def setUp(self):
        super().setUp()
        _seed_document(self.db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        self.user_id = self.register_and_login()

    def test_unauthenticated_returns_401(self):
        client = self.app.test_client()
        resp = client.post("/api/profile/feedback", json={
            "user_id": self.user_id, "knowledge_id": "kw:spark", "action": "useful"})
        self.assertEqual(resp.status_code, 401)

    def test_other_users_id_returns_403(self):
        resp = self.client.post("/api/profile/feedback", json={
            "user_id": "someone-else", "knowledge_id": "kw:spark", "action": "useful"})
        self.assertEqual(resp.status_code, 403)

    def test_invalid_action_returns_400(self):
        resp = self.client.post("/api/profile/feedback", json={
            "user_id": self.user_id, "knowledge_id": "kw:spark", "action": "not-a-valid-action"})
        self.assertEqual(resp.status_code, 400)

    def test_success_returns_feedback_record(self):
        resp = self.client.post("/api/profile/feedback", json={
            "user_id": self.user_id, "knowledge_id": "kw:spark", "action": "useful"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["action"], "useful")

    def test_confirmed_known_triggers_reclassification(self):
        rec_resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        before = next(i for i in rec_resp.get_json()["items"] if i["knowledge_id"] == "kw:spark")
        self.assertEqual(before["status"], "new")

        self.client.post("/api/profile/feedback", json={
            "user_id": self.user_id, "knowledge_id": "kw:spark", "action": "confirmed_known"})

        rec_resp = self.client.get(f"/api/recommendations?user_id={self.user_id}&limit=10")
        after = next(i for i in rec_resp.get_json()["items"] if i["knowledge_id"] == "kw:spark")
        self.assertIn(after["status"], ("known", "refine"))


if __name__ == "__main__":
    unittest.main()
