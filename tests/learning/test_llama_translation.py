import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from knowpipe.learning.llama_provider import LlamaTranslator, sentence_spans
from knowpipe.learning.local_providers import configured_translator, configured_goal_translator
from knowpipe.learning.providers import ProviderUnavailable
from knowpipe.learning.quality import validate_segments


class LlamaTranslationTests(unittest.TestCase):
    def provider(self, completion=None):
        provider = LlamaTranslator('/unused/model.gguf', 'http://127.0.0.1:8089')
        provider._preflight = Mock()

        def request(path, payload, deadline):
            if path == '/apply-template':
                return {'prompt': payload['messages'][1]['content']}
            if path == '/tokenize':
                return {'tokens': [1] * (len(payload['content']) + 20)}
            return completion or {'content': '准确译文。', 'stop_type': 'eos', 'truncated': False}

        provider._request = Mock(side_effect=request)
        return provider

    def test_shared_local_provider_and_remote_endpoint_rejection(self):
        config = {'KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH': '/unused/model.gguf',
                  'KNOWPIPE_LLAMA_TRANSLATION_URL': 'http://127.0.0.1:8089'}
        self.assertIs(configured_translator(config), configured_goal_translator(config))
        for url in ('https://example.com', 'http://127.0.0.1:8089/private', 'http://user@127.0.0.1:8089'):
            with self.assertRaises(ValueError):
                LlamaTranslator('/unused/model.gguf', url)

    def test_sentence_boundaries_preserve_all_characters_and_common_abbreviations(self):
        text = 'Use an option, e.g. a file. Next sentence.\n中文第一句。第二句！'
        spans = list(sentence_spans(text))
        self.assertEqual(''.join(spans), text)
        self.assertEqual(spans[0], 'Use an option, e.g. a file. ')
        self.assertEqual(spans[1], 'Next sentence.\n')
        self.assertEqual(spans[2:], ['中文第一句。', '第二句！'])

    def test_complete_sentences_are_separate_calls_and_alignment_is_real(self):
        provider = self.provider()
        original = 'Write the log first. Only then write the data.'
        output = provider.translate(original, 'en', 'zh')
        validate_segments(original, output.text, output.segments)
        prompts = [call.args[1]['prompt'] for call in provider._request.call_args_list if call.args[0] == '/completion']
        self.assertEqual(prompts, ['Write the log first.', 'Only then write the data.'])

    def test_real_token_budget_splits_before_call_and_preserves_all_source_offsets(self):
        provider = self.provider()
        original = 'word ' * 900
        result = provider.translate(original, 'en', 'zh')
        self.assertGreater(len(result.segments), 1)
        validate_segments(original, result.text, result.segments)
        requests = [call for call in provider._request.call_args_list if call.args[0] == '/completion']
        self.assertTrue(all(len(call.args[1]['prompt']) + 20 <= 1024 for call in requests))
        self.assertTrue(all(call.args[1]['n_predict'] == 2048 for call in requests))
        self.assertEqual(''.join(original[s['source_start']:s['source_end']] for s in result.segments), original)

    def test_budget_or_context_stop_cannot_publish_partial_output(self):
        for stop, truncated in [('limit', False), ('eos', True), ('word', False), ('eos', None)]:
            with self.assertRaisesRegex(ValueError, 'translation_truncated'):
                self.provider({'content': '半句', 'stop_type': stop, 'truncated': truncated}).translate('Complete sentence.', 'en', 'zh')

    def test_code_never_sent_to_translation_and_is_aligned_exactly(self):
        provider = self.provider()
        original = 'Use `x = 20`.\n\n```python\nprint("literal")\n```\n'
        result = provider.translate(original, 'en', 'zh')
        validate_segments(original, result.text, result.segments)
        self.assertIn('`x = 20`', result.text)
        self.assertIn('print("literal")', result.text)
        prompts = [call.args[1]['prompt'] for call in provider._request.call_args_list if call.args[0] == '/completion']
        self.assertTrue(all('x = 20' not in prompt and 'print(' not in prompt for prompt in prompts))

    def test_preflight_rejects_different_weights_or_too_small_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.gguf'
            path.write_bytes(b'model')
            provider = LlamaTranslator(path, 'http://127.0.0.1:8089')
            for served, context, engine in [('/different.gguf', 4096, '4762ad73'),
                                            (str(path), 2048, '4762ad73'), (str(path), 4096, 'other-engine')]:
                provider._request = Mock(return_value={'model_path': served,
                    'default_generation_settings': {'n_ctx': context}, 'build_info': engine})
                with self.assertRaises(ProviderUnavailable):
                    provider.translate('Hello.', 'en', 'zh')
            provider._request = Mock(return_value={'model_path': str(path),
                'default_generation_settings': {'n_ctx': 4096}, 'build_info': 'b6000-4762ad73'})
            provider._preflight(time.monotonic() + 10)


if __name__ == '__main__':
    unittest.main()
