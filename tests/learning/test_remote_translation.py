import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

import mongomock

from knowpipe.learning import content, providers
from knowpipe.learning.local_providers import (configured_translator, configured_goal_translator,
                                              configured_language_provider)
from knowpipe.learning.remote_translation import BigModelTranslator, paragraph_spans, _NoRedirect
from knowpipe.learning.quality import check_translation, validate_segments


def completion(text='完整译文。', finish='stop', model=BigModelTranslator.MODEL):
    return {'model': model, 'choices': [{'finish_reason': finish, 'message': {'content': text}}]}


class RemoteTranslationTests(unittest.TestCase):
    def provider(self):
        return BigModelTranslator('test-secret-never-publish')

    def test_default_shared_remote_ignores_old_local_paths(self):
        config = {'KNOWPIPE_BIGMODEL_API_KEY': 'test-config-secret',
                  'KNOWPIPE_NLLB_MODEL_PATH': '/unused',
                  'KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH': '/unused'}
        provider = configured_translator(config)
        self.assertIsInstance(provider, BigModelTranslator)
        self.assertIs(provider, configured_goal_translator(config))
        self.assertIs(provider, configured_language_provider(config))

    def test_missing_key_unknown_provider_never_falls_back(self):
        for config in ({'KNOWPIPE_TRANSLATION_MODEL_PATH': '/unused'},
                       {'KNOWPIPE_TRANSLATION_PROVIDER': 'bigmodel-free'},
                       {'KNOWPIPE_TRANSLATION_PROVIDER': 'paid', 'KNOWPIPE_BIGMODEL_API_KEY': 'secret'}):
            for factory in (configured_translator, configured_goal_translator, configured_language_provider):
                with self.assertRaisesRegex(providers.ProviderUnavailable, 'translation_not_configured'):
                    factory(config).translate('Hello', 'en', 'zh')

    def test_paragraphs_retain_context_separators_code_and_offsets(self):
        original = 'First sentence.\nSecond sentence with `value = 20`.\n\n```python\nprint("🧠")\n```\nLast sentence.'
        provider = self.provider()
        payloads = []

        def translate(payload, deadline):
            payloads.append(payload)
            user = json.loads(payload['messages'][1]['content'])
            if user['protected']:
                token = next(iter(user['protected']))
                self.assertEqual(user['protected'][token], '`value = 20`')
                self.assertIn('First sentence.\nSecond sentence', user['text'])
                return completion('第一句。\n第二句包含 ' + token + '。')
            return completion('最后一句。')

        provider._request = Mock(side_effect=translate)
        result = provider.translate(original, 'en', 'zh')
        self.assertEqual(len(payloads), 2)
        self.assertIn('```python\nprint("🧠")\n```', result.text)
        self.assertIn('`value = 20`', result.text)
        self.assertEqual(check_translation(original, result.text)['status'], 'checks_passed')
        self.assertTrue(result.complete)
        self.assertEqual(validate_segments(original, result.text, result.segments), list(result.segments))
        self.assertEqual(''.join(span for _, span in paragraph_spans(original)), original)

    def test_model_cannot_change_code_or_silently_truncate(self):
        for output in (completion(finish='length'), completion(model='paid-model'), completion('未保留代码')):
            provider = self.provider()
            provider._request = Mock(return_value=output)
            with self.assertRaises(ValueError):
                provider.translate('Use `some_call()`.', 'en', 'zh')

    def test_completion_unknown_empty_or_missing_is_not_complete(self):
        for output in ({}, completion(''), completion(finish=None), {'model': BigModelTranslator.MODEL, 'choices': []}):
            provider = self.provider()
            provider._request = Mock(return_value=output)
            with self.assertRaises(ValueError):
                provider.translate('Complete source.', 'en', 'zh')

    def test_reverse_goal_translation_uses_same_contract(self):
        provider = self.provider()
        provider._request = Mock(return_value=completion('Limit concurrent downloads.'))
        result = provider.translate('限制并发下载数量。', 'zh-CN', 'en')
        self.assertEqual(result.language, 'en')
        self.assertIn('from Simplified Chinese into English',
                      provider._request.call_args.args[0]['messages'][0]['content'])

    def test_unrecognized_language_and_oversized_paragraph_rejected_before_http(self):
        provider = self.provider()
        provider._request = Mock()
        with self.assertRaises(providers.ProviderUnavailable):
            provider.translate('Hello', 'und', 'zh')
        with self.assertRaisesRegex(ValueError, 'translation_paragraph_too_long'):
            provider.translate('a' * 6001, 'en', 'zh')
        provider._request.assert_not_called()

    def test_identity_excludes_key_and_changes_for_explicit_cache_revision(self):
        first = BigModelTranslator('secret-a')
        second = BigModelTranslator('secret-b')
        self.assertEqual(first.processor_id, second.processor_id)
        self.assertNotIn('secret', first.processor_id)
        self.assertNotEqual(first.processor_id, BigModelTranslator('secret-a', revision='v2').processor_id)

    def response(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        return response

    def test_http_contract_fixed_free_model_and_no_redirects(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.return_value = self.response(completion())
        provider.translate('Translate this.', 'en', 'zh')
        request = provider._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, BigModelTranslator.ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret-never-publish')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'glm-4.7-flash')
        self.assertFalse(payload['stream'])
        self.assertLessEqual(provider._opener.open.call_args.kwargs['timeout'], 90)
        self.assertIsNone(_NoRedirect().redirect_request(request, None, 302, '', {}, 'https://evil.test'))

    def test_rate_limit_retries_are_bounded_and_obey_short_retry_after(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.side_effect = [
            urllib.error.HTTPError(provider.ENDPOINT, 429, 'private-body', {'Retry-After': '3'}, io.BytesIO()),
            self.response(completion())]
        with patch('knowpipe.learning.remote_translation.time.sleep') as sleep:
            provider.translate('Source.', 'en', 'zh')
        sleep.assert_called_once_with(3)
        self.assertEqual(provider._opener.open.call_count, 2)

    def test_persistent_rate_limit_is_safe_unavailable_not_ready(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.side_effect = [urllib.error.HTTPError(
            provider.ENDPOINT, 429, 'secret-remote-error', {}, io.BytesIO()) for _ in range(3)]
        db = mongomock.MongoClient().db
        key = {'source': 'docs', 'doc_id': 'remote-test'}
        db.documents.insert_one(key.copy())
        content.publish_fulltext(db, **key, text='Original source.', language='en')
        with patch('knowpipe.learning.remote_translation.time.sleep'):
            view = providers.translate_document(db, **key, provider=provider)
        self.assertFalse(view['chinese_ready'])
        self.assertEqual(view['processing']['translation']['error_code'], 'translation_rate_limited')
        self.assertEqual(provider._opener.open.call_count, 3)
        self.assertNotIn('secret', json.dumps(view))

    def test_success_publication_reuses_version_bound_cache_and_alignment(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.return_value = self.response(completion('写入日志，然后再写入数据。'))
        db = mongomock.MongoClient().db
        key = {'source': 'docs', 'doc_id': 'remote-success'}
        db.documents.insert_one(key.copy())
        content.publish_fulltext(db, **key, text='Write the log, then write the data.', language='en')
        first = providers.translate_document(db, **key, provider=provider)
        self.assertTrue(first['chinese_ready'])
        self.assertTrue(first['translation_segments'])
        self.assertFalse(first['translation_quality']['semantic_verified'])
        second = providers.translate_document(db, **key, provider=provider)
        self.assertEqual(first['chinese_text'], second['chinese_text'])
        self.assertEqual(provider._opener.open.call_count, 1)
        content.publish_fulltext(db, **key, text='Changed complete source.', language='en')
        self.assertFalse(content.content_view(db.documents.find_one(key))['chinese_ready'])

    def test_network_timeout_is_safe_and_never_uses_a_fallback(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.side_effect = TimeoutError('private endpoint details')
        with self.assertRaisesRegex(providers.ProviderUnavailable, '^translation_remote_unavailable$'):
            provider.translate('Source.', 'en', 'zh')
        self.assertEqual(provider._opener.open.call_count, 1)

    def test_long_retry_after_returns_without_blocking(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.side_effect = urllib.error.HTTPError(
            provider.ENDPOINT, 429, 'private', {'Retry-After': '120'}, io.BytesIO())
        with patch('knowpipe.learning.remote_translation.time.sleep') as sleep:
            with self.assertRaisesRegex(providers.ProviderUnavailable, 'translation_rate_limited'):
                provider.translate('Source.', 'en', 'zh')
        sleep.assert_not_called()

    def test_authentication_error_does_not_retry_or_reveal_body(self):
        provider = self.provider()
        provider._opener = Mock()
        provider._opener.open.side_effect = urllib.error.HTTPError(
            provider.ENDPOINT, 401, 'private-key=secret', {}, io.BytesIO())
        with self.assertRaisesRegex(providers.ProviderUnavailable, '^translation_remote_unavailable$'):
            provider.translate('Source.', 'en', 'zh')
        self.assertEqual(provider._opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
