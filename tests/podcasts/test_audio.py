import io
import json
import tempfile
import unittest
import shutil
import wave
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from knowpipe.learning.providers import TextResult, ProviderUnavailable
from knowpipe.learning.local_providers import configured_transcriber
from knowpipe.podcasts.audio import AudioLimits, download_audio, normalize_audio, transcribe_url, validate_audio
from knowpipe.podcasts.network import FetchError


class AudioTests(unittest.TestCase):
    def test_configured_but_missing_models_never_download_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = configured_transcriber({'KNOWPIPE_ASR_MODEL_PATH': directory})
            for transcriber in (provider, provider.with_context(title='Real episode title')):
                with patch('knowpipe.podcasts.audio.download_audio') as download, \
                     self.assertRaises(ProviderUnavailable):
                    transcribe_url('https://example.com/audio.mp3', transcriber)
                download.assert_not_called()

    def test_preflight_rejects_partial_local_model_without_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('model.bin', 'config.json', 'tokenizer.json'):
                (Path(directory) / name).write_bytes(b'present')
            provider = configured_transcriber({'KNOWPIPE_ASR_MODEL_PATH': directory})
            with patch('knowpipe.podcasts.audio.download_audio') as download, \
                 patch.object(provider, '_load') as load, self.assertRaises(ProviderUnavailable):
                transcribe_url('https://example.com/audio.mp3', provider.with_context(title='Episode'))
            download.assert_not_called()
            load.assert_not_called()

    def test_plain_protocol_provider_without_preflight_still_works(self):
        class PlainTranscriber:
            def transcribe(self, path):
                return TextResult('Complete actual provider contract result.', 'en')
        with patch('knowpipe.podcasts.audio.download_audio') as download, \
             patch('knowpipe.podcasts.audio.validate_audio', return_value=1), \
             patch('knowpipe.podcasts.audio.normalize_audio', side_effect=lambda path, dest, limits: path):
            result = transcribe_url('https://example.com/audio.mp3', PlainTranscriber())
        download.assert_called_once()
        self.assertTrue(result.complete)

    def test_private_addresses_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(FetchError):
            download_audio('http://127.0.0.1/audio.mp3', Path(directory) / 'audio')

    def response(self, payload=b'123456', headers=None, status=200):
        response = Mock(status=status)
        response.getheader.side_effect = lambda name, default=None: (headers or {}).get(name, default)
        response.read1.side_effect = io.BytesIO(payload).read
        return response

    def fetch(self, response, path, limits):
        connection = Mock()
        connection.getresponse.return_value = response
        target = (urlsplit('http://example.com/audio'), 'example.com', 80, '93.184.216.34')
        with patch('knowpipe.podcasts.audio.public_target', return_value=target), \
             patch('knowpipe.podcasts.audio.socket.create_connection'), \
             patch('knowpipe.podcasts.audio.http.client.HTTPConnection', return_value=connection):
            return download_audio('http://example.com/audio', path, limits)

    def test_download_stream_limit_without_content_length(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audio'
            with self.assertRaisesRegex(FetchError, 'audio_too_large'):
                self.fetch(self.response(), path, AudioLimits(max_bytes=5))
            self.assertFalse(path.exists())

    def test_content_length_and_partial_http_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for response in [self.response(headers={'Content-Length': '100'}), self.response(status=206)]:
                path = Path(directory) / 'audio'
                with self.assertRaises(FetchError):
                    self.fetch(response, path, AudioLimits(max_bytes=5))
                self.assertFalse(path.exists())

    def test_failed_download_does_not_delete_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audio'
            path.write_bytes(b'keep me')
            with self.assertRaises(FetchError):
                self.fetch(self.response(), path, AudioLimits())
            self.assertEqual(path.read_bytes(), b'keep me')

    def test_redirect_is_revalidated(self):
        response = self.response(headers={'Location': 'http://127.0.0.1/private'}, status=302)
        connection = Mock()
        connection.getresponse.return_value = response
        target = (urlsplit('http://example.com/audio'), 'example.com', 80, '93.184.216.34')
        with tempfile.TemporaryDirectory() as directory, \
             patch('knowpipe.podcasts.audio.public_target', side_effect=[target, FetchError('non_public_address')]) as validate, \
             patch('knowpipe.podcasts.audio.socket.create_connection'), \
             patch('knowpipe.podcasts.audio.http.client.HTTPConnection', return_value=connection):
            with self.assertRaises(FetchError):
                download_audio('http://example.com/audio', Path(directory) / 'audio')
            self.assertEqual(validate.call_args.args[0], 'http://127.0.0.1/private')

    def test_probe_rejects_duration_missing_audio_and_nonfinite_values(self):
        for payload in [dict(format={'duration': '7201'}, streams=[{'codec_type': 'audio'}]),
                        dict(format={'duration': '1'}, streams=[]),
                        dict(format={'duration': 'nan'}, streams=[{'codec_type': 'audio'}])]:
            with patch('knowpipe.podcasts.audio.subprocess.run', return_value=Mock(stdout=json.dumps(payload), returncode=0)), \
                 self.assertRaises(ValueError):
                validate_audio(Path('/tmp/audio'), AudioLimits())

    def test_cleanup_on_transcriber_failure_and_partial_result(self):
        seen = []
        def fake_download(url, path, limits):
            path.write_bytes(b'audio')
            seen.append(path)
        for provider in [Mock(transcribe=Mock(side_effect=RuntimeError('bad'))),
                         Mock(transcribe=Mock(return_value=TextResult('partial', 'en', False)))]:
            with patch('knowpipe.podcasts.audio.download_audio', side_effect=fake_download), \
                 patch('knowpipe.podcasts.audio.validate_audio', return_value=2), \
                 patch('knowpipe.podcasts.audio.normalize_audio', side_effect=lambda path, dest, limits: path), \
                 self.assertRaises((RuntimeError, ValueError)):
                transcribe_url('https://example.com/audio', provider)
            self.assertFalse(seen[-1].exists())

    def test_probe_restricts_protocols_and_formats(self):
        payload = dict(format={'duration': '1'}, streams=[{'codec_type': 'audio'}])
        with patch('knowpipe.podcasts.audio.subprocess.run', return_value=Mock(stdout=json.dumps(payload), returncode=0)) as run:
            self.assertEqual(validate_audio(Path('/tmp/audio'), AudioLimits()), 1)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[arguments.index('-protocol_whitelist') + 1], 'file')
        self.assertNotIn('hls', arguments[arguments.index('-format_whitelist') + 1].split(','))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg needed')
    def test_real_decode_enforces_duration_even_without_trusting_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\0\0' * 16000)
            self.assertEqual(validate_audio(source), 1)
            with self.assertRaisesRegex(ValueError, 'audio_duration_exceeded'):
                normalize_audio(source, Path(directory) / 'output.wav', AudioLimits(max_duration_seconds=0.5))
