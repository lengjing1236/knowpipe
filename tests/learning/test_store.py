import unittest
from concurrent.futures import ThreadPoolExecutor

import mongomock

from knowpipe.learning import content, store
from knowpipe.web import mongo_sink
from knowpipe.web.score_job import rows_for_profile


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        mongo_sink.ensure_indexes(self.db)
        for source in ["arxiv", "stackexchange"]:
            self.db.documents.insert_one({"source": source, "doc_id": "same/id", "title": source,
                                          "body_text": "Original", "language": "en"})

    def mark(self, db, user_id, source, doc_id, read):
        doc = db.documents.find_one({"source": source, "doc_id": doc_id})
        return store.mark_read(db, user_id, source, doc_id, read, content.content_version(doc) if doc else None)

    def test_goal_updates_do_not_clobber_history_or_known_keywords(self):
        mongo_sink.save_known_topics(self.db, "u1", ["0"], ["spark"])
        self.mark(self.db, "u1", "arxiv", "same/id", True)
        store.save_goal(self.db, "u1", "理解数据库事务")
        profile = mongo_sink.get_or_create_profile(self.db, "u1")
        self.assertEqual(profile["known_keywords"], ["spark"])
        self.assertEqual(len(profile["read_doc_ids"]), 1)
        self.assertEqual(store.profile_view(self.db, "u1")["revision"], 2)
        store.save_goal(self.db, "u1", "理解数据库事务")
        self.assertEqual(store.profile_view(self.db, "u1")["revision"], 2)

    def test_idempotence_source_identity_and_account_isolation(self):
        self.mark(self.db, "u1", "arxiv", "same/id", True)
        before = mongo_sink.get_or_create_profile(self.db, "u1")["read_doc_ids"][0]
        self.mark(self.db, "u1", "arxiv", "same/id", True)
        self.assertEqual(mongo_sink.get_or_create_profile(self.db, "u1")["read_doc_ids"][0], before)
        self.mark(self.db, "u1", "stackexchange", "same/id", True)
        self.assertEqual(store.profile_view(self.db, "u1")["read_count"], 2)
        self.assertEqual(store.profile_view(self.db, "u2")["read_count"], 0)
        self.db.documents.delete_many({})
        self.mark(self.db, "u1", "arxiv", "same/id", False)
        self.mark(self.db, "u1", "arxiv", "same/id", False)
        self.assertEqual(store.profile_view(self.db, "u1")["revision"], 3)

    def test_concurrent_different_and_same_marks_do_not_lose_updates(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.mark(self.db, "u1", ["arxiv", "stackexchange"][i % 2], "same/id", True), range(20)))
        self.assertEqual(store.profile_view(self.db, "u1")["read_count"], 2)
        self.assertEqual(store.profile_view(self.db, "u1")["revision"], 2)

    def test_history_keeps_missing_documents_and_changed_versions(self):
        self.mark(self.db, "u1", "arxiv", "same/id", True)
        self.db.documents.update_one({"source": "arxiv"}, {"$set": {"body_text": "Changed"}})
        self.assertEqual(store.list_history(self.db, "u1")["items"][0]["version_status"], "changed")
        self.db.documents.delete_one({"source": "arxiv"})
        self.assertEqual(store.list_history(self.db, "u1")["items"][0]["version_status"], "unavailable")

    def test_old_reference_preserves_unknown_timestamp_and_version(self):
        mongo_sink.get_or_create_profile(self.db, "u1")
        self.db.user_profiles.update_one({"user_id": "u1"}, {"$set": {"read_doc_ids": [{"source": "arxiv", "doc_id": "same/id"}]}})
        entry = store.list_history(self.db, "u1")["items"][0]
        self.assertIsNone(entry["read_at"])
        self.assertEqual(entry["version_status"], "unknown")

    def test_catalog_paginates_without_returning_bodies_and_query_is_literal(self):
        result = store.list_documents(self.db, "u1", page=2, limit=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertNotIn("original_text", result["items"][0])
        self.assertEqual(store.list_documents(self.db, "u1", q=".*")["total"], 0)

    def test_extended_reference_still_supports_legacy_read_penalty(self):
        self.mark(self.db, "u1", "arxiv", "same/id", True)
        profile = mongo_sink.get_or_create_profile(self.db, "u1")
        units = [{"source": "arxiv", "doc_id": "same/id", "knowledge_id": "kw:a", "keywords": ["a"]}]
        extended = rows_for_profile(units, profile, {}, "u1")
        legacy = rows_for_profile(units, dict(profile, read_doc_ids=[{"source": "arxiv", "doc_id": "same/id"}]), {}, "u1")
        self.assertEqual(extended, legacy)
