"""knowpipe.web.baseline 的测试：有已读文档时相似度排序，无已读时最新优先。"""
import unittest
from datetime import datetime, timedelta, timezone

import mongomock

from knowpipe.web import baseline


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_web_test"]


def _seed_document(db, source, doc_id, similar_doc_ids=None, created_at=None):
    db.documents.insert_one({
        "source": source, "doc_id": doc_id, "title": f"title-{doc_id}",
        "source_url": f"https://example.com/{doc_id}", "created_at": created_at,
        "mining": {"batch_id": "b1", "topic_cluster_id": 0, "keyword_count": 1},
    })
    db.mining_results.insert_one({
        "doc_id": doc_id, "source": source, "batch_id": "b1",
        "keywords": [{"term": "spark", "weight": 0.5}],
        "topic_cluster_id": 0, "similar_doc_ids": similar_doc_ids or [], "reliable": True,
    })


class TestBaselineWithReadHistory(unittest.TestCase):
    def test_uses_similar_doc_ids_from_read_documents(self):
        db = _make_db()
        _seed_document(db, "arxiv", "a1", similar_doc_ids=["a2", "a3"])
        _seed_document(db, "arxiv", "a2")
        _seed_document(db, "arxiv", "a3")

        items = baseline.get_baseline_recommendations(
            db, read_doc_ids=[{"source": "arxiv", "doc_id": "a1"}], limit=10)
        doc_ids = {item["document"]["doc_id"] for item in items}
        self.assertEqual(doc_ids, {"a2", "a3"})

    def test_does_not_recommend_already_read_documents(self):
        db = _make_db()
        _seed_document(db, "arxiv", "a1", similar_doc_ids=["a1", "a2"])
        _seed_document(db, "arxiv", "a2")

        items = baseline.get_baseline_recommendations(
            db, read_doc_ids=[{"source": "arxiv", "doc_id": "a1"}], limit=10)
        doc_ids = {item["document"]["doc_id"] for item in items}
        self.assertNotIn("a1", doc_ids)


class TestBaselineWithoutReadHistory(unittest.TestCase):
    def test_falls_back_to_newest_first(self):
        db = _make_db()
        now = datetime.now(timezone.utc)
        _seed_document(db, "arxiv", "old", created_at=now - timedelta(days=2))
        _seed_document(db, "arxiv", "new", created_at=now)

        items = baseline.get_baseline_recommendations(db, read_doc_ids=[], limit=10)
        doc_ids = [item["document"]["doc_id"] for item in items]
        self.assertEqual(doc_ids[0], "new")


if __name__ == "__main__":
    unittest.main()
