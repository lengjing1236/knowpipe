import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from knowpipe.learning.providers import ProviderUnavailable, TextResult
from knowpipe.learning.local_providers import (LocalTranslator, LocalTranscriber,
    configured_translator, configured_transcriber, translation_spans, transcript_paragraphs)


class LocalProviderTests(unittest.TestCase):
    def test_factories_do_not_download_missing_models(self):
        with self.assertRaises(ProviderUnavailable):
            configured_translator({}).translate('hello', 'en', 'zh')
        with self.assertRaises(ProviderUnavailable):
            configured_transcriber({}).transcribe(Path('/missing.wav'))

    def test_missing_tokenizer_cannot_trigger_library_network_fallback(self):
        constructor = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.bin').write_bytes(b'weights')
            (root / 'audio.wav').write_bytes(b'audio')
            with patch.dict('sys.modules', {'faster_whisper': SimpleNamespace(WhisperModel=constructor)}), \
                 self.assertRaises(ProviderUnavailable):
                LocalTranscriber(root).transcribe(root / 'audio.wav')
        constructor.assert_not_called()

    def test_translation_spans_preserve_every_character_and_code(self):
        text = 'First paragraph.\n\n```python\nprint("hello")\n```\n\nUse `x = 1` here.\n    x = 2\n'
        spans = list(translation_spans(text))
        self.assertEqual(''.join(part for _, part in spans), text)
        protected = ''.join(part for translate, part in spans if not translate)
        self.assertIn('```python\nprint("hello")\n```', protected)
        self.assertIn('`x = 1`', protected)
        self.assertIn('    x = 2', protected)

    def translator(self, hypotheses=None):
        translator = LocalTranslator('/unused')
        translator._tokenizer = Mock()
        translator._tokenizer.encode.side_effect = lambda text, out_type=str: text.split()
        translator._tokenizer.decode.side_effect = lambda pieces: ''.join(pieces)
        translator._model = Mock()
        translator._model.translate_batch.side_effect = lambda batch, **kwargs: [
            SimpleNamespace(hypotheses=[hypotheses or ['中文', '</s>']]) for _ in batch]
        return translator

    def test_full_translation_protects_code_and_processes_all_spans(self):
        translator = self.translator()
        result = translator.translate('First paragraph.\n\n```python\nx = 1\n```\n\nLast paragraph.', 'en', 'zh')
        self.assertEqual(result.language, 'zh')
        self.assertTrue(result.complete)
        self.assertEqual(result.text.count('中文'), 2)
        self.assertIn('```python\nx = 1\n```', result.text)

    def test_truncated_translation_is_rejected_instead_of_returning_partial(self):
        translator = self.translator(['部分'])
        with self.assertRaisesRegex(ValueError, 'translation_truncated'):
            translator.translate('Translate all of this.', 'en', 'zh')

    def test_long_text_is_split_without_silent_input_truncation(self):
        translator = self.translator()
        translator.translate(' '.join('word' for _ in range(650)), 'en', 'zh')
        calls = translator._model.translate_batch.call_args_list
        self.assertGreater(len(calls), 1)
        self.assertEqual(sum(len(c.args[0][0]) for c in calls), 650)
        self.assertTrue(all(c.kwargs['max_input_length'] == 0 for c in calls))

    def test_unsupported_pair_and_empty_output_fail(self):
        with self.assertRaises(ProviderUnavailable):
            self.translator().translate('Bonjour', 'fr', 'zh')
        with self.assertRaises(ProviderUnavailable):
            self.translator().translate('unknown language', 'und', 'zh')
        with self.assertRaises(ValueError):
            self.translator(['</s>']).translate('Hello', 'en', 'zh')

    def test_asr_consumes_whole_generator(self):
        transcriber = LocalTranscriber('/unused')
        transcriber._model = Mock()
        transcriber._model.transcribe.return_value = (
            iter([SimpleNamespace(text=' First sentence.'), SimpleNamespace(text=' Last sentence.')]),
            SimpleNamespace(language='en'))
        with tempfile.NamedTemporaryFile() as audio:
            result = transcriber.transcribe(Path(audio.name))
        self.assertEqual(result, TextResult('First sentence.\n\nLast sentence.', 'en'))

    def test_asr_windows_do_not_break_translation_sentence_context(self):
        text = transcript_paragraphs(['An unfinished database', 'transaction is rolled back.',
                                     'The final sentence.'], 'en')
        self.assertEqual(text, 'An unfinished database transaction is rolled back.\n\nThe final sentence.')
        self.assertEqual(transcript_paragraphs(['数据库', '事务回滚。'], 'zh'), '数据库事务回滚。')

    def test_optional_metadata_context_uses_same_local_model_and_does_not_leak(self):
        transcriber = LocalTranscriber('/unused')
        transcriber._model = Mock()
        transcriber._model.transcribe.side_effect = lambda *args, **kwargs: (
            iter([SimpleNamespace(text='A complete sentence.')]), SimpleNamespace(language='en'))
        contextual = transcriber.with_context(title='tmpfs and ECS', feed_title='AWS Podcast')
        with tempfile.NamedTemporaryFile() as audio:
            contextual.transcribe(Path(audio.name))
            transcriber.transcribe(Path(audio.name))
        calls = transcriber._model.transcribe.call_args_list
        self.assertEqual(calls[0].kwargs['hotwords'], 'AWS Podcast. tmpfs and ECS')
        self.assertIsNone(calls[1].kwargs['hotwords'])

    def test_asr_generator_failure_does_not_publish_partial(self):
        def broken():
            yield SimpleNamespace(text='Only first sentence')
            raise RuntimeError('decode failed')
        transcriber = LocalTranscriber('/unused')
        transcriber._model = Mock()
        transcriber._model.transcribe.return_value = broken(), SimpleNamespace(language='en')
        with tempfile.NamedTemporaryFile() as audio, self.assertRaises(RuntimeError):
            transcriber.transcribe(Path(audio.name))

    def test_silence_is_not_a_successful_full_transcript(self):
        transcriber = LocalTranscriber('/unused')
        transcriber._model = Mock()
        transcriber._model.transcribe.return_value = iter([]), SimpleNamespace(language='en')
        with tempfile.NamedTemporaryFile() as audio, self.assertRaises(ValueError):
            transcriber.transcribe(Path(audio.name))
