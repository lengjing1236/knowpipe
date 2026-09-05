"""knowpipe.mining.mongo_sink 的测试，使用 mongomock。"""
import unittest

import mongomock

from knowpipe.mining import mongo_sink


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_mining_test"]


class TestMongoSink(unittest.TestCase):
    def test_upsert_documents_overwrites_on_same_key(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        doc = {"source": "stackexchange", "doc_id": "1", "title": "old title"}
        mongo_sink.upsert_documents(db, [doc])

        updated = {"source": "stackexchange", "doc_id": "1", "title": "new title"}
        mongo_sink.upsert_documents(db, [updated])

        self.assertEqual(db.documents.count_documents({}), 1)
        self.assertEqual(db.documents.find_one({"doc_id": "1"})["title"], "new title")

    def test_repeat_upsert_with_unchanged_content_does_not_duplicate(self):
        """对应 FR-012/SC-004：内容不变时重复 upsert 不产生重复的 documents 记录。"""
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        doc = {"source": "stackexchange", "doc_id": "1", "title": "same title",
               "body_text": "same body"}

        mongo_sink.upsert_documents(db, [doc])
        mongo_sink.upsert_documents(db, [dict(doc)])
        mongo_sink.upsert_documents(db, [dict(doc)])

        self.assertEqual(db.documents.count_documents({}), 1)
        stored = db.documents.find_one({"doc_id": "1"})
        self.assertEqual(stored["title"], "same title")
        self.assertEqual(stored["body_text"], "same body")

    def test_repeat_upsert_with_changed_content_updates_not_duplicates(self):
        """对应 FR-012/SC-004：内容变更后重新 upsert 以新内容覆盖同一条记录，而非新增。"""
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        original = {"source": "stackexchange", "doc_id": "1", "title": "v1",
                    "body_text": "original body"}
        mongo_sink.upsert_documents(db, [original])

        changed = {"source": "stackexchange", "doc_id": "1", "title": "v2",
                   "body_text": "updated body"}
        mongo_sink.upsert_documents(db, [changed])

        self.assertEqual(db.documents.count_documents({}), 1)
        stored = db.documents.find_one({"doc_id": "1"})
        self.assertEqual(stored["title"], "v2")
        self.assertEqual(stored["body_text"], "updated body")

    def test_mining_results_accumulates_history_across_batches_without_deleting_old(self):
        """对应 quickstart.md Step 3：mining_results 按批次追加历史记录（不覆盖删除），
        documents.mining 摘要引用则指向最新一次批次结果。"""
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.upsert_documents(db, [{"source": "arxiv", "doc_id": "a1"}])

        result_batch1 = {
            "doc_id": "a1", "source": "arxiv", "batch_id": "b1",
            "keywords": [{"term": "spark", "weight": 1.0}],
            "topic_cluster_id": 0, "similar_doc_ids": [], "reliable": True,
        }
        mongo_sink.write_mining_results(db, [result_batch1])

        result_batch2 = {
            "doc_id": "a1", "source": "arxiv", "batch_id": "b2",
            "keywords": [{"term": "spark", "weight": 1.0}, {"term": "mongo", "weight": 0.8}],
            "topic_cluster_id": 1, "similar_doc_ids": [], "reliable": True,
        }
        mongo_sink.write_mining_results(db, [result_batch2])

        self.assertEqual(db.mining_results.count_documents({"doc_id": "a1"}), 2)
        self.assertIsNotNone(db.mining_results.find_one({"doc_id": "a1", "batch_id": "b1"}))
        self.assertIsNotNone(db.mining_results.find_one({"doc_id": "a1", "batch_id": "b2"}))

        doc = db.documents.find_one({"doc_id": "a1"})
        self.assertEqual(doc["mining"]["batch_id"], "b2")
        self.assertEqual(doc["mining"]["topic_cluster_id"], 1)

    def test_mining_results_queryable_by_doc_id_and_batch_id(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        mongo_sink.upsert_documents(db, [{"source": "arxiv", "doc_id": "a1"}])
        result = {
            "doc_id": "a1", "source": "arxiv", "batch_id": "b1",
            "keywords": [{"term": "spark", "weight": 1.0}],
            "topic_cluster_id": 0, "similar_doc_ids": [], "reliable": True,
        }
        mongo_sink.write_mining_results(db, [result])

        found = db.mining_results.find_one({"doc_id": "a1", "batch_id": "b1"})
        self.assertIsNotNone(found)
        doc = db.documents.find_one({"doc_id": "a1"})
        self.assertEqual(doc["mining"]["batch_id"], "b1")


if __name__ == "__main__":
    unittest.main()
