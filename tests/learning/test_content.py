import unittest

import mongomock

from knowpipe.learning import content
from knowpipe.learning.providers import TextResult, UnconfiguredProvider, translate_document


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.db.documents.insert_one({"source": "arxiv", "doc_id": "old/123", "title": "资料",
                                      "language": "en", "body_text": "An abstract."})

    def doc(self):
        return self.db.documents.find_one({"source": "arxiv", "doc_id": "old/123"})

    def publish(self, text="Original full text.\n\n    print('hello')", language="en"):
        return content.publish_fulltext(self.db, "arxiv", "old/123", text, language)

    def test_legacy_content_is_never_assumed_fulltext(self):
        self.assertEqual(content.content_view(self.doc())["content_status"], "abstract")
        for source, expected in [("stackexchange", "question_only"), ("docs", "unverified")]:
            self.assertEqual(content.content_view(dict(self.doc(), source=source))["content_status"], expected)
        self.assertEqual(content.content_view(dict(self.doc(), body_text=""))["content_status"], "missing")

    def test_public_error_is_bounded_and_does_not_survive_version_change(self):
        self.publish()
        content.set_stage_state(self.db, self.doc(), "translation", "failed", "translation_truncated")
        state = content.content_view(self.doc())["processing"]["translation"]
        self.assertEqual(state["error_code"], "translation_truncated")
        content.set_stage_state(self.db, self.doc(), "translation", "failed", "private provider output")
        self.assertNotIn("error_code", content.content_view(self.doc())["processing"]["translation"])
        content.set_stage_state(self.db, self.doc(), "translation", "failed", "translation_timeout")
        self.db.documents.update_one({}, {"$set": {"body_text": "External version change"}})
        self.assertNotIn("error_code", content.content_view(self.doc())["processing"]["translation"])

    def test_chinese_fulltext_preserves_format_and_english_waits_for_translation(self):
        text = "事务示例\n\n    begin;\n    commit;"
        self.publish(text, "zh-CN")
        view = content.content_view(self.doc())
        self.assertEqual(view["chinese_text"], text)
        self.assertTrue(view["chinese_ready"])
        self.publish()
        self.assertIsNone(content.content_view(self.doc())["chinese_text"])

    def test_old_translation_is_invalid_after_update_and_cannot_overwrite(self):
        self.publish()
        version = content.content_version(self.doc())
        self.assertTrue(content.publish_translation(self.db, "arxiv", "old/123", version, "完整译文"))
        self.assertEqual(content.content_view(self.doc())["chinese_text"], "完整译文")
        self.publish("Changed original.")
        self.assertIsNone(content.content_view(self.doc())["chinese_text"])
        self.assertFalse(content.publish_translation(self.db, "arxiv", "old/123", version, "过期译文"))

    def test_same_fulltext_keeps_translation_and_analysis_invalidates_on_change(self):
        self.publish()
        version = content.content_version(self.doc())
        content.publish_translation(self.db, "arxiv", "old/123", version, "完整译文")
        self.publish()
        self.assertEqual(content.content_view(self.doc())["chinese_text"], "完整译文")
        self.db.documents.update_one({}, {"$set": {"mining": {"batch_id": "b1"}}})
        self.publish("A changed paper.")
        self.assertIsNone(self.doc()["mining"])

    def test_external_body_change_does_not_reuse_old_content_or_translation(self):
        self.publish()
        version = content.content_version(self.doc())
        content.publish_translation(self.db, "arxiv", "old/123", version, "旧译文")
        self.db.documents.update_one({}, {"$set": {"body_text": "Changed outside the publisher"}})
        view = content.content_view(self.doc())
        self.assertEqual(view["content_status"], "unverified")
        self.assertFalse(view["chinese_ready"])
        self.assertEqual(view["processing"]["translation"]["status"], "stale")

    def test_provider_unavailable_failure_and_partial_are_not_success(self):
        self.publish()
        translate_document(self.db, "arxiv", "old/123", UnconfiguredProvider())
        self.assertEqual(content.content_view(self.doc())["processing"]["translation"]["status"], "unavailable")

        class Partial:
            def translate(self, *args):
                return TextResult("只有一段", "zh", complete=False)

        translate_document(self.db, "arxiv", "old/123", Partial())
        self.assertEqual(content.content_view(self.doc())["processing"]["translation"]["status"], "failed")
        self.assertIsNone(content.content_view(self.doc())["chinese_text"])

    def test_success_is_cached_and_stale_provider_result_is_discarded(self):
        self.publish()
        calls = []

        class Translator:
            def translate(self, text, source_language, target_language):
                calls.append(text)
                return TextResult("全文中文\n\n    print('hello')", "zh")

        translate_document(self.db, "arxiv", "old/123", Translator())
        translate_document(self.db, "arxiv", "old/123", Translator())
        self.assertEqual(len(calls), 1)

        self.publish("A new version")
        owner = self

        class RacingTranslator:
            def translate(self, *args):
                owner.publish("Another version while translation runs")
                return TextResult("已经过期", "zh")

        translate_document(self.db, "arxiv", "old/123", RacingTranslator())
        self.assertIsNone(content.content_view(self.doc())["chinese_text"])

    def test_forged_empty_and_wrong_language_results_are_rejected(self):
        with self.assertRaises(ValueError):
            self.publish("   ")
        self.publish()
        for value in ["", "   "]:
            with self.assertRaises(ValueError):
                content.publish_translation(self.db, "arxiv", "old/123", content.content_version(self.doc()), value)
        self.db.documents.update_one({}, {"$set": {"translation": {
            "source_version": content.content_version(self.doc()), "complete": False, "language": "zh", "text": "部分"
        }}})
        self.assertIsNone(content.content_view(self.doc())["chinese_text"])

    def test_current_analysis_attempt_is_visible_despite_historical_result(self):
        self.publish()
        self.db.documents.update_one({}, {"$set": {"mining": {"batch_id": "old", "content_version": "old"}}})
        for status in ["queued", "running", "failed", "unavailable"]:
            content.set_stage_state(self.db, self.doc(), "analysis", status)
            self.assertEqual(content.content_view(self.doc())["processing"]["analysis"]["status"], status)

    def test_failure_and_wrong_language_do_not_publish_ready(self):
        self.publish()

        class WrongLanguage:
            def translate(self, *args):
                return TextResult("Still English", "en")

        class Failure:
            def translate(self, *args):
                raise RuntimeError("provider exception")

        for provider in [WrongLanguage(), Failure()]:
            view = translate_document(self.db, "arxiv", "old/123", provider)
            self.assertEqual(view["processing"]["translation"]["status"], "failed")
            self.assertFalse(view["chinese_ready"])
