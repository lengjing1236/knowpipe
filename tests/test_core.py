import os
import tempfile
import unittest

from knowpipe.brain import Brain, BrainError
from knowpipe.cli import _load_batch_checkpoint, _save_batch_checkpoint
from knowpipe.report import build_article_report
from knowpipe.store import MemoryStore


class CoreBehaviorTests(unittest.TestCase):
    def test_article_report_does_not_include_transcripts(self):
        report = build_article_report(
            "标题", "source", "RAW TRANSCRIPT", "CLEAN TRANSCRIPT", "正文", "manual"
        )
        self.assertNotIn("RAW TRANSCRIPT", report)
        self.assertNotIn("CLEAN TRANSCRIPT", report)
        self.assertIn("正文", report)

    def test_checkpoint_only_resumes_matching_successful_pages(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "job.json")
            rows = [
                {"page": 1, "error": None},
                {"page": 2, "error": "temporary failure"},
            ]
            _save_batch_checkpoint(path, "BVtest", [1, 2], "heuristic", rows)
            resumed = _load_batch_checkpoint(path, "BVtest", [1, 2], "heuristic")
            self.assertEqual([row["page"] for row in resumed], [1])
            self.assertEqual(
                _load_batch_checkpoint(path, "BVtest", [1, 2], "openai"), []
            )

    def test_memory_index_is_invalidated_after_new_card(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryStore(os.path.join(root, "cards.jsonl"))
            store.add_new("事件循环调度任务", "test")
            store.candidates_for("事件循环")
            self.assertIsNotNone(store._index)
            store.add_new("新的向量检索知识", "test", persist=False)
            self.assertIsNone(store._index)
            store.save()

    def test_invalid_llm_verdict_is_rejected(self):
        with self.assertRaises(BrainError):
            Brain._normalize_verdicts(
                [{"index": 0, "verdict": "invalid", "confidence": 2}], 1
            )


if __name__ == "__main__":
    unittest.main()
