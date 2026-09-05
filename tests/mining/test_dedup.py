"""knowpipe.mining.dedup 的单元测试。"""
import unittest

from knowpipe.mining.dedup import (
    content_hash,
    exact_key,
    is_content_duplicate,
    is_exact_duplicate,
)


class TestDedup(unittest.TestCase):
    def test_exact_key_same_source_and_url(self):
        rec1 = {"source": "stackexchange", "source_url": "https://x.com/1"}
        rec2 = {"source": "stackexchange", "source_url": "https://x.com/1"}
        seen = {exact_key(rec1)}
        self.assertTrue(is_exact_duplicate(rec2, seen))

    def test_exact_key_different_url_not_duplicate(self):
        rec1 = {"source": "stackexchange", "source_url": "https://x.com/1"}
        rec2 = {"source": "stackexchange", "source_url": "https://x.com/2"}
        seen = {exact_key(rec1)}
        self.assertFalse(is_exact_duplicate(rec2, seen))

    def test_content_hash_cross_source_duplicate(self):
        rec1 = {"title": "Same Title", "body_text": "Same body text here."}
        rec2 = {"title": "  same title  ", "body_text": "SAME BODY TEXT HERE."}
        seen = {content_hash(rec1)}
        self.assertTrue(is_content_duplicate(rec2, seen))

    def test_content_hash_different_content_not_duplicate(self):
        rec1 = {"title": "Title A", "body_text": "Body A"}
        rec2 = {"title": "Title B", "body_text": "Body B"}
        seen = {content_hash(rec1)}
        self.assertFalse(is_content_duplicate(rec2, seen))


if __name__ == "__main__":
    unittest.main()
