"""knowpipe.web.score_job 的测试：用 local[*] SparkSession 跑最小样例，校验分数产出与 batch_id。"""
import unittest
from datetime import datetime, timezone

import mongomock

from knowpipe.web import mongo_sink, score_job


def _make_db():
    client = mongomock.MongoClient()
    return client["knowpipe_web_test"]


def _seed_document(db, source, doc_id, keywords, topic_cluster_id, reliable=True):
    db.documents.insert_one({
        "source": source, "doc_id": doc_id, "title": f"title-{doc_id}",
        "source_url": f"https://example.com/{doc_id}", "created_at": datetime.now(timezone.utc),
        "mining": {"batch_id": "b1", "topic_cluster_id": topic_cluster_id,
                    "keyword_count": len(keywords)},
    })
    db.mining_results.insert_one({
        "doc_id": doc_id, "source": source, "batch_id": "b1",
        "keywords": [{"term": k, "weight": w} for k, w in keywords],
        "topic_cluster_id": topic_cluster_id, "similar_doc_ids": [], "reliable": reliable,
    })


class TestScoreJob(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = SparkSession.builder.master("local[*]").appName("test-score-job").getOrCreate()

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_run_produces_scores_and_batch_stats(self):
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "a1", [("spark", 0.9)], topic_cluster_id=0)
        mongo_sink.create_user(db, "u1", "alice", "hash")
        mongo_sink.save_known_topics(db, "u1", ["0"], ["spark"])

        batch_id, stats = score_job.run(db, self.spark)

        self.assertEqual(stats["status"], "success")
        stored_batch = db.batches.find_one({"batch_id": batch_id})
        self.assertIsNotNone(stored_batch)
        self.assertIsNotNone(stored_batch["started_at"])
        self.assertIsNotNone(stored_batch["finished_at"])

        record = db.user_knowledge.find_one({"user_id": "u1", "knowledge_id": "kw:spark"})
        self.assertIsNotNone(record)
        self.assertEqual(record["batch_id"], batch_id)
        self.assertIsInstance(record["score"], float)

    def test_score_reflects_topic_relevance_and_quality(self):
        """命中已知主题簇 + 可靠文档的分数应高于未命中 + 不可靠文档的分数。"""
        db = _make_db()
        mongo_sink.ensure_indexes(db)
        _seed_document(db, "arxiv", "known-topic", [("spark", 0.9)], topic_cluster_id=0, reliable=True)
        _seed_document(db, "arxiv", "unreliable", [("other", 0.5)], topic_cluster_id=9, reliable=False)
        mongo_sink.create_user(db, "u1", "alice", "hash")
        mongo_sink.save_known_topics(db, "u1", ["0"], [])

        score_job.run(db, self.spark)

        rows = list(db.user_knowledge.find({"user_id": "u1"}))
        by_id = {r["knowledge_id"]: r["score"] for r in rows}
        self.assertGreater(by_id["topic:0"], by_id["topic:9"])


if __name__ == "__main__":
    unittest.main()
