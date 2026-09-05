"""knowpipe.web.mongo_sink 的测试，使用 mongomock。"""
import unittest

import mongomock

from knowpipe.web import mongo_sink


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_web_test"]


def _seed_document(db, source, doc_id, keywords, topic_cluster_id, batch_id="b1",
                    similar_doc_ids=None, reliable=True, created_at=None):
    from datetime import datetime, timezone
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


class TestUsers(unittest.TestCase):
    def test_create_user_and_find(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.create_user(db, "u1", "alice", "hash1")

        self.assertEqual(mongo_sink.find_user_by_username(db, "alice")["user_id"], "u1")
        self.assertEqual(mongo_sink.find_user_by_id(db, "u1")["username"], "alice")

    def test_duplicate_username_raises(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.create_user(db, "u1", "alice", "hash1")
        with self.assertRaises(mongo_sink.UsernameTakenError):
            mongo_sink.create_user(db, "u2", "alice", "hash2")


class TestUserProfiles(unittest.TestCase):
    def test_get_or_create_profile_defaults(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        profile = mongo_sink.get_or_create_profile(db, "u1")
        self.assertEqual(profile["known_topics"], [])
        self.assertEqual(profile["known_keywords"], [])
        self.assertEqual(db.user_profiles.count_documents({"user_id": "u1"}), 1)

        again = mongo_sink.get_or_create_profile(db, "u1")
        self.assertEqual(db.user_profiles.count_documents({"user_id": "u1"}), 1)
        self.assertEqual(again["user_id"], "u1")

    def test_save_known_topics_upserts(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.save_known_topics(db, "u1", ["0"], ["spark"])
        profile = db.user_profiles.find_one({"user_id": "u1"})
        self.assertEqual(profile["known_topics"], ["0"])
        self.assertEqual(profile["known_keywords"], ["spark"])

    def test_append_feedback(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.append_feedback(db, "u1", "kw:spark", "useful")
        profile = db.user_profiles.find_one({"user_id": "u1"})
        self.assertEqual(len(profile["feedback_history"]), 1)
        self.assertEqual(profile["feedback_history"][0]["action"], "useful")


class TestUserKnowledge(unittest.TestCase):
    def test_composite_unique_index_upsert_no_duplicates(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        record = {"knowledge_id": "kw:spark", "source": "arxiv", "doc_id": "a1",
                  "status": "new", "matched_keywords": [], "matched_topic_cluster_id": None}
        mongo_sink.upsert_user_knowledge_status(db, "u1", [record])
        mongo_sink.upsert_user_knowledge_status(db, "u1", [dict(record, status="known")])

        self.assertEqual(db.user_knowledge.count_documents({"user_id": "u1"}), 1)
        stored = db.user_knowledge.find_one({"user_id": "u1", "knowledge_id": "kw:spark"})
        self.assertEqual(stored["status"], "known")

    def test_write_score_batch_does_not_overwrite_status(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.upsert_user_knowledge_status(db, "u1", [{
            "knowledge_id": "kw:spark", "status": "new", "matched_keywords": [],
            "matched_topic_cluster_id": None,
        }])
        mongo_sink.write_score_batch(db, [{"user_id": "u1", "knowledge_id": "kw:spark", "score": 0.7}], "b1")

        stored = db.user_knowledge.find_one({"user_id": "u1", "knowledge_id": "kw:spark"})
        self.assertEqual(stored["status"], "new")
        self.assertEqual(stored["score"], 0.7)
        self.assertEqual(stored["batch_id"], "b1")


class TestTopicAggregations(unittest.TestCase):
    def test_get_topic_aggregations_groups_by_cluster(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "a1", [("spark", 0.9), ("mongo", 0.5)], topic_cluster_id=0)
        _seed_document(db, "arxiv", "a2", [("spark", 0.8)], topic_cluster_id=0)
        _seed_document(db, "stackexchange", "s1", [("docker", 0.7)], topic_cluster_id=1)

        aggregations = mongo_sink.get_topic_aggregations(db)
        by_id = {a["topic_cluster_id"]: a for a in aggregations}
        self.assertEqual(by_id[0]["document_count"], 2)
        self.assertIn("spark", by_id[0]["top_keywords"])
        self.assertEqual(by_id[1]["document_count"], 1)
        # 按 document_count 降序排列
        self.assertEqual(aggregations[0]["topic_cluster_id"], 0)

    def test_get_knowledge_units_includes_topic_and_keyword_units(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)

        units = mongo_sink.get_knowledge_units(db)
        knowledge_ids = {u["knowledge_id"] for u in units}
        self.assertIn("topic:0", knowledge_ids)
        self.assertIn("kw:spark", knowledge_ids)

    def test_derive_known_keywords_from_topics_and_read_docs(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        _seed_document(db, "arxiv", "a2", [("mongo", 0.6)], topic_cluster_id=1)

        keywords = mongo_sink.derive_known_keywords(
            db, known_topics=["0"], read_doc_ids=[{"source": "arxiv", "doc_id": "a2"}])
        self.assertIn("spark", keywords)
        self.assertIn("mongo", keywords)


class TestDocumentDetail(unittest.TestCase):
    def test_get_document_detail_found(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        detail = mongo_sink.get_document_detail(db, "arxiv", "a1")
        self.assertEqual(detail["doc_id"], "a1")
        self.assertEqual(detail["mining"]["topic_cluster_id"], 0)

    def test_get_document_detail_not_found(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        self.assertIsNone(mongo_sink.get_document_detail(db, "arxiv", "missing"))


if __name__ == "__main__":
    unittest.main()
