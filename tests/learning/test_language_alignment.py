import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import mongomock

from knowpipe.learning import content, providers
from knowpipe.learning.local_providers import (NllbTranslator, LocalTranslator,
    configured_goal_translator, configured_translator, configured_language_provider)
from knowpipe.learning.quality import check_translation, validate_segments


class LanguageAlignmentTests(unittest.TestCase):
    def nllb(self):
        provider = NllbTranslator('/unused')
        provider._tokenizer, provider._model = Mock(), Mock()
        provider._tokenizer.encode.side_effect = lambda text, out_type=str: list(text)
        provider._tokenizer.decode.side_effect = lambda pieces: ''.join(pieces)
        provider._model.translate_batch.side_effect = lambda batch, **options: [SimpleNamespace(
            hypotheses=[[options['target_prefix'][0][0], '译文', '</s>']])]
        return provider

    def test_shared_factory_uses_one_bilingual_instance_without_loading(self):
        config = {'KNOWPIPE_NLLB_MODEL_PATH': '/unused/shared'}
        forward, reverse = configured_translator(config), configured_goal_translator(config)
        self.assertIs(forward, reverse)
        self.assertIs(forward, configured_language_provider(config))
        self.assertIsNone(forward._model)

    def test_every_source_character_has_actual_correspondence_and_code_is_exact(self):
        provider = self.nllb()
        original = 'First sentence.\n\nUse `value = 20`.\n\n```python\nprint("🧠")\n```\nLast sentence.'
        output = provider.translate(original, 'en', 'zh')
        segments = validate_segments(original, output.text, output.segments)
        self.assertEqual(''.join(original[s['source_start']:s['source_end']] for s in segments), original)
        self.assertEqual(''.join(output.text[s['target_start']:s['target_end']] for s in segments), output.text)
        self.assertIn('print("🧠")', output.text)
        self.assertIn('`value = 20`', output.text)
        self.assertEqual(output.segments[-1]['source_end'], len(original))

    def test_language_markers_change_per_call_and_long_input_is_not_truncated(self):
        provider = self.nllb()
        provider.translate('中' * 481, 'zh', 'en')
        calls = provider._model.translate_batch.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(len(call.args[0][0]) - 2 for call in calls), 481)
        self.assertTrue(all(call.args[0][0][0] == 'zho_Hans' for call in calls))
        self.assertTrue(all(call.kwargs['max_input_length'] == 0 for call in calls))
        provider.translate('English sentence.', 'en', 'zh')
        last = provider._model.translate_batch.call_args
        self.assertEqual(last.args[0][0][0], 'eng_Latn')
        self.assertEqual(last.kwargs['target_prefix'], [['zho_Hans']])

    def test_no_end_token_cannot_claim_complete_translation(self):
        provider = self.nllb()
        provider._model.translate_batch.side_effect = None
        provider._model.translate_batch.return_value = [SimpleNamespace(hypotheses=[['zho_Hans', '部分']])]
        with self.assertRaisesRegex(ValueError, 'translation_truncated'):
            provider.translate('Translate all of this.', 'en', 'zh')

    def test_alignment_rejects_gaps_false_protected_text_and_partial_coverage(self):
        invalid = [
            [{'source_start': 1, 'source_end': 3, 'target_start': 0, 'target_end': 2, 'kind': 'text'}],
            [{'source_start': 0, 'source_end': 3, 'target_start': 0, 'target_end': 2, 'kind': 'protected'}],
            [{'source_start': 0, 'source_end': 2, 'target_start': 0, 'target_end': 2, 'kind': 'text'}],
        ]
        for segments in invalid:
            with self.assertRaisesRegex(ValueError, 'invalid_translation_alignment'):
                validate_segments('abc', '中文', segments)

    def test_alignment_is_published_only_with_its_source_version_and_processor(self):
        db = mongomock.MongoClient().db
        key = {'source': 'docs', 'doc_id': 'aligned'}
        db.documents.insert_one(key.copy())
        content.publish_fulltext(db, **key, text='A sentence.', language='en')
        aligned = ({'source_start': 0, 'source_end': 11, 'target_start': 0, 'target_end': 5, 'kind': 'text'},)
        provider = Mock(processor_id='alignment-v1')
        provider.translate.return_value = providers.TextResult('一个句子。', 'zh', segments=aligned)
        view = providers.translate_document(db, **key, provider=provider)
        self.assertTrue(view['chinese_ready'])
        self.assertEqual(view['translation_segments'], list(aligned))
        self.assertNotIn('translation_segments', content.content_view(db.documents.find_one(key), include_text=False))
        content.publish_fulltext(db, **key, text='Updated text.', language='en')
        self.assertEqual(content.content_view(db.documents.find_one(key))['translation_segments'], [])

    def test_legacy_provider_does_not_invent_alignment(self):
        self.assertEqual(validate_segments('English', '中文', ()), [])
        self.assertEqual(providers.TextResult('中文', 'zh').segments, ())

    def test_repeated_number_and_identifier_damage_are_visible(self):
        added = check_translation('Section 20.', '第20节，第20节。')
        self.assertIn('number_added', added['issues'])
        damaged = check_translation('Set max_connections before restart.', '在重启前设置连接上限。')
        self.assertIn('identifier_missing', damaged['issues'])
        valid = check_translation('Set max_connections before restart.', '在重启前设置 max_connections。')
        self.assertEqual(valid['status'], 'checks_passed')
        self.assertFalse(valid['semantic_verified'])

    def test_unrepresentable_output_is_not_silently_presented_as_chinese_ready(self):
        result = check_translation('Recover after a crash.', '崩 ⁇ 后恢复。')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('unknown_symbol', result['issues'])

    def test_ordinary_abbreviations_are_not_treated_as_program_identifiers(self):
        result = check_translation('Use an option, e.g., a file.', '使用选项，例如文件。')
        self.assertEqual(result['status'], 'checks_passed')


if __name__ == '__main__':
    unittest.main()
