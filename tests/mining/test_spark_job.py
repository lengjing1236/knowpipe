"""knowpipe.mining.spark_job 的测试，使用 local[*] SparkSession。"""
import unittest
from unittest.mock import patch

import mongomock

from knowpipe.mining import batch as batch_mod
from knowpipe.mining import contract as contract_mod
from knowpipe.mining import spark_job
from knowpipe.mining.spark_job import run_mining

SPARK = None


def _get_spark():
    global SPARK
    if SPARK is None:
        from pyspark.sql import SparkSession
        SPARK = (SparkSession.builder.master("local[*]")
                 .appName("test-spark-job").getOrCreate())
    return SPARK


def _sample_records():
    topics = {
        "spark": "Apache Spark distributed computing engine cluster processing big data batch",
        "mongo": "MongoDB document database collection query index storage nosql",
    }
    records = []
    for i in range(10):
        topic_key = "spark" if i % 2 == 0 else "mongo"
        raw = {
            "doc_id": f"doc{i}",
            "source": "stackexchange",
            "source_site": "stackoverflow",
            "title": f"Question {i}",
            "body_text": f"{topics[topic_key]} example number {i} more words to pad content out",
            "language": "en",
            "created_at": "2026-01-01T00:00:00Z",
            "source_url": f"https://example.com/{i}",
        }
        records.append(contract_mod.validate_document(raw))
    return records


class TestSparkJob(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        global SPARK
        if SPARK is not None:
            SPARK.stop()
            SPARK = None

    def test_min_slice_produces_keywords_and_clusters(self):
        spark = _get_spark()
        records = _sample_records()
        stats = batch_mod.BatchStats("test-batch", ["stackexchange"])

        results = run_mining(spark, records, top_k=5, num_clusters=2, stats=stats)

        self.assertEqual(len(results), len(records))
        for doc_id, mined in results.items():
            self.assertTrue(len(mined["keywords"]) > 0, f"{doc_id} has no keywords")
            self.assertIsInstance(mined["topic_cluster_id"], int)
        self.assertEqual(stats.failed_count, 0)

    def test_empty_body_after_tokenize_counted_as_failed(self):
        spark = _get_spark()
        raw = {
            "doc_id": "empty1",
            "source": "stackexchange",
            "source_site": "stackoverflow",
            "title": "T",
            "body_text": "123 456 789",
            "language": "en",
            "created_at": "2026-01-01T00:00:00Z",
            "source_url": "https://example.com/empty1",
        }
        record = contract_mod.validate_document(raw)
        stats = batch_mod.BatchStats("test-batch", ["stackexchange"])

        results = run_mining(spark, [record], top_k=5, num_clusters=1, stats=stats)

        self.assertEqual(results, {})
        self.assertEqual(stats.failed_count, 1)


class TestMainWritesBatchStatsOnFailure(unittest.TestCase):
    """对应 FR-010：即便处理运行在 Spark 阶段失败，也必须留存可核查的运行记录。"""

    def test_batch_stats_written_with_failed_status_when_run_mining_raises(self):
        db = mongomock.MongoClient()["knowpipe_test"]

        fake_record = {
            "doc_id": "d1", "source": "stackexchange", "source_site": "stackoverflow",
            "title": "t", "body_text": "spark mongo cluster words enough to pass filters",
            "language": "en", "created_at": "2026-01-01T00:00:00Z",
            "source_url": "https://example.com/d1",
            "quality": {"short_body": False},
        }

        class _FakeSpark:
            def stop(self):
                pass

        class _FakeBuilder:
            def master(self, _):
                return self

            def appName(self, _):
                return self

            def getOrCreate(self):
                return _FakeSpark()

        with patch.object(spark_job, "prepare_valid_records", return_value=[fake_record]), \
             patch("pyspark.sql.SparkSession") as mock_session_cls, \
             patch("pymongo.MongoClient") as mock_client_cls, \
             patch.object(spark_job, "run_mining", side_effect=RuntimeError("boom")):
            mock_session_cls.builder = _FakeBuilder()
            mock_client_cls.return_value.get_default_database.return_value = db

            with self.assertRaises(RuntimeError):
                spark_job.main(["--sources", "stackexchange", "--target-count", "1",
                                 "--mongo-uri", "mongodb://unused/knowpipe_test"])

        stored = db.batches.find_one({})
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "failed")
        self.assertIn("boom", stored["error_message"])
        self.assertIsNotNone(stored["finished_at"])


if __name__ == "__main__":
    unittest.main()
