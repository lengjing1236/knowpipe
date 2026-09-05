"""knowpipe.mining.contract 的单元测试。"""
import unittest

from knowpipe.mining.contract import (
    DocumentValidationError,
    SHORT_BODY_WORD_THRESHOLD,
    validate_document,
)


def _base_record(**overrides):
    record = {
        "doc_id": "q1",
        "source": "stackexchange",
        "source_site": "stackoverflow",
        "title": "How to use Spark",
        "body_text": " ".join(["word"] * (SHORT_BODY_WORD_THRESHOLD + 10)),
        "language": "en",
        "created_at": "2026-01-01T00:00:00Z",
        "source_url": "https://stackoverflow.com/q/1",
    }
    record.update(overrides)
    return record


class TestValidateDocument(unittest.TestCase):
    def test_missing_required_field_rejected(self):
        record = _base_record()
        del record["title"]
        with self.assertRaises(DocumentValidationError):
            validate_document(record)

    def test_invalid_source_rejected(self):
        record = _base_record(source="reddit")
        with self.assertRaises(DocumentValidationError):
            validate_document(record)

    def test_valid_record_gets_defaults_and_quality(self):
        record = validate_document(_base_record())
        self.assertEqual(record["body_raw"], "")
        self.assertEqual(record["tags"], [])
        self.assertEqual(record["license"], "unknown")
        self.assertIsNone(record["mining"])
        self.assertFalse(record["quality"]["short_body"])

    def test_short_body_flagged(self):
        record = validate_document(_base_record(body_text="too short"))
        self.assertTrue(record["quality"]["short_body"])


if __name__ == "__main__":
    unittest.main()
