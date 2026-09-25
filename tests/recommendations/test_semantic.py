import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mongomock
import numpy as np

from knowpipe.learning.providers import TextResult
from knowpipe.recommendations.semantic import (
    EnglishText, LocalSemantic, SemanticUnavailable, UnsupportedSemanticInput,
    configured_semantic,
)


class Translation:
    processor_id = 'test-translation-v1'

    def __init__(self, text='PostgreSQL transactions preserve consistency.'):
        self.text, self.calls = text, 0

    def translate(self, text, source, target):
        self.calls += 1
        return TextResult(self.text, 'en')


class SemanticProviderTests(unittest.TestCase):
    def test_missing_assets_and_no_implicit_download_or_inference(self):
        self.assertIsNone(configured_semantic({}))
        with tempfile.TemporaryDirectory() as root:
            provider = LocalSemantic(root)
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertIn('unavailable', provider.processor_id)
            with self.assertRaisesRegex(SemanticUnavailable, 'semantic_model_unavailable'):
                provider.check_available()
            self.assertEqual(provider._models, {})

    def test_chinese_requires_explicit_successful_conversion_and_versions_cache(self):
        db = mongomock.MongoClient().db
        first = Translation()
        text = EnglishText(first, db.semantic_translations)
        self.assertEqual(text.convert('PostgreSQL事务保持一致性。'), first.text)
        self.assertEqual(text.convert('PostgreSQL事务保持一致性。'), first.text)
        self.assertEqual(first.calls, 1)
        second = Translation()
        second.processor_id = 'test-translation-v2'
        EnglishText(second, db.semantic_translations).convert('PostgreSQL事务保持一致性。')
        self.assertEqual(second.calls, 1)
        self.assertEqual(db.semantic_translations.count_documents({}), 2)
        with self.assertRaisesRegex(SemanticUnavailable, 'semantic_translation_unavailable'):
            EnglishText().convert('事务')
        invalid = Translation('事务')
        with self.assertRaisesRegex(SemanticUnavailable, 'semantic_translation_failed'):
            EnglishText(invalid, db.semantic_translations).convert('事务')
        self.assertEqual(db.semantic_translations.count_documents({}), 2)
        self.assertEqual(text.convert('valid English evidence'), 'valid English evidence')

    def test_conversion_cache_and_inputs_are_bounded(self):
        text = EnglishText(Translation())
        for number in range(260):
            text.convert(f'事务{number}')
        self.assertEqual(len(text._local), 256)
        with self.assertRaises(UnsupportedSemanticInput):
            text.convert('中' * 4001)

    def test_inputs_never_silently_truncate_or_accept_untranslated_chinese(self):
        provider = LocalSemantic('/unused')
        with self.assertRaisesRegex(UnsupportedSemanticInput, 'semantic_english_required'):
            provider.relevance('事务', ['Transactions preserve consistency.'])
        with self.assertRaisesRegex(UnsupportedSemanticInput, 'semantic_batch_limit_exceeded'):
            provider.encode(['word'] * 513)
        tokenizer = SimpleNamespace(encode_batch=lambda values: [SimpleNamespace(ids=[1] * 257)])
        provider._model = lambda name: (tokenizer, None)
        with self.assertRaisesRegex(UnsupportedSemanticInput, 'semantic_token_limit_exceeded'):
            provider.encode(['words'])

    def test_model_feature_math_and_nli_direction_labels(self):
        provider = LocalSemantic('/unused')
        calls = []

        def batches(name, values):
            calls.append((name, values))
            if name == 'rank':
                yield np.asarray([[0.], [2.]]), np.asarray([[1], [1]])
            elif name == 'embed':
                yield np.asarray([[[3., 0.], [0., 4.], [99., 99.]]]), np.asarray([[1, 1, 0]])
            else:
                yield np.asarray([[0., 8., 0.], [0., 0., 8.]]), np.asarray([[1], [1]])

        provider._batches = batches
        self.assertEqual(provider.relevance('query', ['one', 'two'])[0], .5)
        self.assertTrue(np.allclose(provider.encode(['text']), [[.6, .8]]))
        inference = provider.infer([('history', 'candidate'), ('different', 'candidate')])
        self.assertGreater(inference[0]['entailment'], .99)
        self.assertGreater(inference[1]['neutral'], .99)
        self.assertEqual(calls[-1][1][0], ('history', 'candidate'))


if __name__ == '__main__':
    unittest.main()
