import unittest

import mongomock

from knowpipe.learning import content
from knowpipe.learning.providers import ProviderUnavailable, TextResult, translate_document


class FailureProvider:
    def __init__(self, error):
        self.error = error

    def translate(self, *args):
        raise self.error


class ResultProvider:
    def __init__(self, result):
        self.result = result

    def translate(self, *args):
        return self.result


class ProviderErrorTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.key = {"source": "official_docs", "doc_id": "transactions"}
        self.db.documents.insert_one(dict(self.key))
        content.publish_fulltext(self.db, **self.key, text="A complete source document.", language="en")

    def assert_rejected(self, provider, status, code):
        view = translate_document(self.db, **self.key, provider=provider)
        doc = self.db.documents.find_one(self.key)
        self.assertEqual(doc["processing"]["translation"], {
            "status": status, "source_version": content.content_version(doc), "error_code": code})
        self.assertFalse(view["chinese_ready"])
        self.assertNotIn("translation", doc)

    def test_known_failure_codes_preserve_failure_and_fulltext_boundary(self):
        cases = [
            (ValueError("empty_translation"), "translation_empty_output"),
            (ValueError("translation_truncated"), "translation_truncated"),
            (ValueError("invalid_translation_input"), "translation_invalid_input"),
            (TimeoutError("translation_timeout"), "translation_timeout"),
        ]
        for error, code in cases:
            with self.subTest(code=code):
                self.assert_rejected(FailureProvider(error), "failed", code)

    def test_incomplete_wrong_language_and_empty_outputs_are_not_published(self):
        cases = [
            (TextResult("只有第一段", "zh", complete=False), "translation_incomplete"),
            (TextResult("Still English", "en"), "translation_incomplete"),
            (TextResult("   ", "zh"), "translation_invalid_output"),
        ]
        for result, code in cases:
            with self.subTest(code=code, result=result):
                self.assert_rejected(ResultProvider(result), "failed", code)

    def test_unavailable_causes_are_bounded(self):
        cases = [
            ("translation_model_unavailable", "translation_model_unavailable"),
            ("translation_language_not_supported", "translation_language_not_supported"),
            ("translation_not_configured", "provider_not_configured"),
            ("https://provider.invalid/?token=private", "provider_not_configured"),
        ]
        for message, code in cases:
            with self.subTest(code=code):
                self.assert_rejected(FailureProvider(ProviderUnavailable(message)), "unavailable", code)

    def test_unknown_errors_do_not_leak_exception_messages_or_arbitrary_args(self):
        for error in [
            RuntimeError("https://provider.invalid/?token=private"),
            ValueError("empty_translation: private source text"),
            ValueError("empty_translation", "private source text"),
            ValueError({"private": "source text"}),
            ValueError(),
        ]:
            with self.subTest(error_type=type(error).__name__, args=error.args):
                self.assert_rejected(FailureProvider(error), "failed", "translation_failed")

    def test_known_identifier_with_wrong_exception_type_is_not_misdiagnosed(self):
        self.assert_rejected(FailureProvider(RuntimeError("translation_timeout")),
                             "failed", "translation_failed")


if __name__ == "__main__":
    unittest.main()
