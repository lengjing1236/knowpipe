import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import mongomock

from knowpipe.learning import content, providers
from knowpipe.learning.local_providers import LocalTranslator
from knowpipe.learning.local_providers import configured_goal_translator
from knowpipe.learning.quality import check_translation


class Translator:
    def __init__(self, identity, output):
        self.processor_id, self.output, self.calls = identity, output, 0

    def translate(self, *args):
        self.calls += 1
        return providers.TextResult(self.output, 'zh')


class TranslationQualityTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.key = {'source': 'docs', 'doc_id': 'quality'}
        self.db.documents.insert_one(self.key.copy())
        content.publish_fulltext(self.db, **self.key,
            text='The limit is 20% and 1,024 bytes.\n\nUse `limit = 1024`.\n', language='en')
        self.good = '限制为 20% 和 1,024 字节。\n\n使用 `limit = 1024`。\n'

    def document(self):
        return self.db.documents.find_one(self.key)

    def test_missing_number_blocks_publication_and_is_public(self):
        view = providers.translate_document(self.db, **self.key,
            provider=Translator('first', '限制为百分之二十，使用 `limit = 1024`。'))
        self.assertFalse(view['chinese_ready'])
        self.assertEqual(view['translation_quality']['status'], 'failed')
        self.assertIn('number_missing', view['translation_quality']['issues'])
        self.assertEqual(view['processing']['translation']['error_code'], 'translation_quality_failed')

    def test_missing_code_blocks_even_if_numbers_retained(self):
        view = providers.translate_document(self.db, **self.key,
            provider=Translator('first', '限制为 20% 和 1,024 字节。limit = 1024。'))
        self.assertFalse(view['chinese_ready'])
        self.assertIn('code_missing', view['translation_quality']['issues'])

    def test_good_translation_cached_and_processor_upgrade_recomputes(self):
        first, second = Translator('first', self.good), Translator('second', self.good + ' 新版。')
        first_view = providers.translate_document(self.db, **self.key, provider=first)
        self.assertTrue(first_view['chinese_ready'])
        self.assertFalse(first_view['translation_quality']['semantic_verified'])
        providers.translate_document(self.db, **self.key, provider=first)
        self.assertEqual(first.calls, 1)
        self.assertFalse(providers.translation_matches(self.document(), second))
        view = providers.translate_document(self.db, **self.key, provider=second)
        self.assertEqual(second.calls, 1)
        self.assertEqual(view['translation_processor'], providers.processor_identity(second))
        self.assertTrue(providers.translation_matches(self.document(), second))

    def test_superseded_processor_cannot_publish_success_or_failure(self):
        owner = self
        for failure in (False, True):
            content.publish_fulltext(self.db, **self.key, text='Complete source.' + str(failure), language='en')
            newer = Translator('new', '完整原文。' + str(failure))

            class SlowOld:
                processor_id = 'old'

                def translate(self, *args):
                    providers.translate_document(owner.db, **owner.key, provider=newer)
                    if failure:
                        raise RuntimeError('old failed')
                    return providers.TextResult('旧内容。' + str(failure), 'zh')

            view = providers.translate_document(self.db, **self.key, provider=SlowOld())
            self.assertTrue(view['chinese_ready'])
            self.assertEqual(view['chinese_text'], '完整原文。' + str(failure))
            self.assertEqual(view['translation_processor'], providers.processor_identity(newer))

    def test_legacy_publisher_cannot_overwrite_claimed_processor(self):
        providers.translate_document(self.db, **self.key, provider=Translator('current', self.good))
        self.assertFalse(content.publish_translation(self.db, **self.key,
            source_version=content.content_version(self.document()), text='无身份旧任务'))

    def test_provider_identity_does_not_expose_path_or_unbounded_text(self):
        identity = providers.processor_identity(Translator('/secret/model?token=private', self.good))
        self.assertNotIn('secret', identity)
        self.assertLessEqual(len(identity), 160)

    def test_local_model_identity_changes_when_weights_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'metadata.json').write_text('{"from_code":"en","to_code":"zh"}')
            (root / 'sentencepiece.model').write_bytes(b'tokenizer')
            (root / 'model').mkdir()
            (root / 'model/model.bin').write_bytes(b'first')
            first = providers.processor_identity(LocalTranslator(root))
            (root / 'model/model.bin').write_bytes(b'second')
            second = providers.processor_identity(LocalTranslator(root))
            self.assertNotEqual(first, second)
            adapter_upgrade = LocalTranslator(root)
            adapter_upgrade.ADAPTER_VERSION = 'new-adapter-version'
            self.assertNotEqual(second, providers.processor_identity(adapter_upgrade))
            self.assertNotEqual(second, providers.processor_identity(
                LocalTranslator(root, source_language='zh', target_language='en')))

    def test_reverse_direction_translates_chinese_and_separates_english_chunks(self):
        translator = LocalTranslator('/unused', source_language='zh', target_language='en')
        translator._tokenizer, translator._model = Mock(), Mock()
        translator._tokenizer.encode.side_effect = lambda text, out_type=str: list(text)
        translator._tokenizer.decode.side_effect = lambda pieces: ' '.join(pieces)
        translator._model.translate_batch.return_value = [SimpleNamespace(hypotheses=[['database', '</s>']])]
        output = translator.translate('数' * 193, 'zh', 'en')
        self.assertEqual(output.language, 'en')
        self.assertEqual(output.text, 'database database')

    def test_goal_factory_is_offline_and_uses_reverse_direction(self):
        with self.assertRaises(providers.ProviderUnavailable):
            configured_goal_translator({}).translate('数据库', 'zh', 'en')
        provider = configured_goal_translator({'KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH': '/unused',
                                               'KNOWPIPE_TRANSLATION_PROVIDER': 'local'})
        self.assertEqual((provider.source_language, provider.target_language), ('zh', 'en'))

    def test_number_check_accepts_chinese_adjacency_and_fullwidth_digits(self):
        self.assertEqual(check_translation('Use 20% of 1,024 bytes.', '使用２０％的１０２４字节。')['status'],
                         'checks_passed')

    def test_integrity_does_not_claim_to_detect_semantic_number_relation(self):
        result = check_translation('Reduce by 20%.', '减少至20%。')
        self.assertEqual(result['status'], 'checks_passed')
        self.assertFalse(result['semantic_verified'])

    def test_quality_is_invalidated_by_changed_fulltext(self):
        providers.translate_document(self.db, **self.key, provider=Translator('first', '缺失数字和代码'))
        content.publish_fulltext(self.db, **self.key, text='Updated source.', language='en')
        view = content.content_view(self.document())
        self.assertEqual(view['translation_quality']['status'], 'not_checked')
        self.assertIsNone(view['translation_processor'])

    def test_arbitrary_quality_messages_and_paths_are_not_public(self):
        self.db.documents.update_one(self.key, {'$set': {
            'translation_expected_processor': '/private/model',
            'translation_quality': {'source_version': content.content_version(self.document()),
                                    'processor_id': '/private/model', 'status': 'failed',
                                    'issues': ['code_missing', 'private message', {'secret': 'x'}]}}})
        view = content.content_view(self.document())
        self.assertIsNone(view['translation_processor'])
        self.assertEqual(view['translation_quality']['issues'], ['code_missing'])

    def test_direct_publisher_cannot_publish_known_failed_quality(self):
        quality = check_translation(self.document()['body_text'], '不完整')
        self.assertFalse(content.publish_translation(self.db, **self.key,
            source_version=content.content_version(self.document()), text='不完整', quality=quality))
        self.assertFalse(content.content_view(self.document())['chinese_ready'])

    def test_failed_quality_is_not_reused_as_success_cache(self):
        provider = Translator('first', '缺失数字和代码')
        for _ in range(2):
            self.assertFalse(providers.translate_document(self.db, **self.key, provider=provider)['chinese_ready'])
        self.assertEqual(provider.calls, 2)
        provider.output = self.good
        self.assertTrue(providers.translate_document(self.db, **self.key, provider=provider)['chinese_ready'])
        self.assertEqual(provider.calls, 3)

    def test_old_generation_cannot_reclaim_newer_document_after_reread(self):
        newer = Translator('new', self.good)
        providers.translate_document(self.db, **self.key, provider=newer, generation=20)
        old = Translator('old', self.good + ' 旧模型')
        providers.translate_document(self.db, **self.key, provider=old, generation=10)
        providers.translate_document(self.db, **self.key, provider=old, generation=20)
        providers.translate_document(self.db, **self.key, provider=old)
        self.assertEqual(old.calls, 0)
        self.assertEqual(self.document()['translation_generation'], 20)
        self.assertEqual(self.document()['translation']['processor_id'], 'new')

    def test_old_same_processor_generation_cannot_write_failure_or_quality(self):
        original = self.document()
        self.assertTrue(content.claim_translation(self.db, original, 'same', generation=1))
        observed = self.document()
        self.assertTrue(content.claim_translation(self.db, observed, 'same', generation=2))
        quality = check_translation(observed['body_text'], self.good)
        self.assertFalse(content.publish_translation_quality(self.db, observed, 'same', quality, generation=1))
        self.assertFalse(content.set_stage_state(self.db, observed, 'translation', 'failed',
                                               expected_processor='same', generation=1))
        self.assertFalse(content.publish_translation(self.db, **self.key,
            source_version=content.content_version(observed), text=self.good,
            processor_id='same', quality=quality, generation=1))
        self.assertTrue(content.publish_translation(self.db, **self.key,
            source_version=content.content_version(observed), text=self.good,
            processor_id='same', quality=quality, generation=2))


if __name__ == '__main__':
    unittest.main()
