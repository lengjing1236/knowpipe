"""Explicitly configured, offline CPU providers. Model acquisition is an operator task.

Completion means all source chunks/audio segments were processed, not that model
output is error-free. The small public models are a baseline, not a quality claim.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from threading import RLock

from .providers import ProviderUnavailable, TextResult, UnconfiguredProvider
from .quality import translation_spans


def _append_aligned(parts, segments, original, output, kind):
    source_start = segments[-1]['source_end'] if segments else 0
    target_start = segments[-1]['target_end'] if segments else 0
    parts.append(output)
    segments.append({'source_start': source_start, 'source_end': source_start + len(original),
                     'target_start': target_start, 'target_end': target_start + len(output), 'kind': kind})


def transcript_paragraphs(segments, language):
    """ASR decoding windows are not sentence boundaries; join unfinished sentences.

    Retain complete sentence groups as paragraphs so downstream translation does
    not receive half of a sentence solely because an audio window ended there.
    """
    paragraphs = []
    separator = '' if language in {'zh', 'ja', 'ko'} else ' '
    for text in segments:
        if paragraphs and not re.search(r'[.!?。！？][\"\'”’)]*$', paragraphs[-1]):
            paragraphs[-1] += separator + text
        else:
            paragraphs.append(text)
    return '\n\n'.join(paragraphs)


class LocalTranslator:
    """Configured Argos SentencePiece/CTranslate2 pair, without implicit downloads.

    Model files are immutable for this provider's lifetime. Replace the provider
    (normally restart its worker) when installing a new model version.
    """
    ADAPTER_VERSION = 'argos-ct2-sentencepiece-alignment-v3'

    def __init__(self, model_path, *, source_language='en', target_language='zh', threads=2, timeout_seconds=1800):
        self.model_path = Path(model_path)
        self.source_language = str(source_language).lower().split('-')[0]
        self.target_language = str(target_language).lower().split('-')[0]
        self.threads = max(1, min(int(threads), 8))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._model = self._tokenizer = None
        self._processor_id = None

    @property
    def processor_id(self):
        if self._processor_id is not None:
            return self._processor_id
        digest = hashlib.sha256()
        digest.update(f'{self.ADAPTER_VERSION}:{self.source_language}:{self.target_language}'.encode())
        try:
            files = [self.model_path / 'metadata.json', self.model_path / 'sentencepiece.model']
            files.extend(sorted(path for path in (self.model_path / 'model').rglob('*') if path.is_file()))
            if not (self.model_path / 'model/model.bin').is_file():
                raise FileNotFoundError('model_missing')
            for path in files:
                digest.update(str(path.relative_to(self.model_path)).encode())
                with path.open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
            status = 'argos'
        except OSError:
            # Missing providers at different locations do not share failed caches;
            # the path itself is never published.
            digest.update(str(self.model_path.absolute()).encode())
            status = 'argos-unavailable'
        self._processor_id = f'{status}-{self.source_language}-{self.target_language}-{digest.hexdigest()}'
        return self._processor_id

    def _load(self):
        if self._model is not None:
            return
        try:
            import ctranslate2
            import sentencepiece
            metadata = json.loads((self.model_path / 'metadata.json').read_text())
            if metadata.get('from_code') != self.source_language or metadata.get('to_code') != self.target_language:
                raise ValueError('unsupported_model')
            self._tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(self.model_path / 'sentencepiece.model'))
            self._model = ctranslate2.Translator(str(self.model_path / 'model'), device='cpu',
                                                compute_type='int8', inter_threads=1, intra_threads=self.threads)
        except Exception as exc:
            self._model = self._tokenizer = None
            raise ProviderUnavailable('translation_model_unavailable') from exc

    def translate(self, text, source_language, target_language):
        if (str(source_language).lower().split('-')[0] != self.source_language
                or str(target_language).lower().split('-')[0] != self.target_language):
            # In particular, und is not silently treated as English.
            raise ProviderUnavailable('translation_language_not_supported')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 4_000_000:
            raise ValueError('invalid_translation_input')
        self._load()
        deadline = time.monotonic() + self.timeout_seconds
        parts, segments = [], []
        for translate, span in translation_spans(text):
            source_pattern = r'[\u3400-\u9fffA-Za-z]' if self.source_language == 'zh' else r'[A-Za-z]'
            if not translate or not re.search(source_pattern, span):
                _append_aligned(parts, segments, span, span, 'protected')
                continue
            leading = span[:len(span) - len(span.lstrip())]
            trailing = span[len(span.rstrip()):]
            translated = []
            # Sentence boundaries improve context; token chunks bound every call.
            sentences = re.split(r'(?<=[。！？])\s*|(?<=[.!?])\s+(?=[A-Z])', span.strip())
            for sentence in sentences:
                if not sentence:
                    continue
                tokens = self._tokenizer.encode(sentence, out_type=str)
                for start in range(0, len(tokens), 192):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('translation_timeout')
                    result = self._model.translate_batch([tokens[start:start + 192]], beam_size=2,
                        max_input_length=0, max_decoding_length=1024, return_end_token=True)[0]
                    hypothesis = result.hypotheses[0]
                    if not hypothesis or hypothesis[-1] != '</s>':
                        raise ValueError('translation_truncated')
                    # Some OPUS/Argos target tokens retain SentencePiece's space
                    # marker after decoding. Preserve ordinary API underscores.
                    output = self._tokenizer.decode(hypothesis[:-1]).replace('▁', ' ').strip()
                    if not output:
                        raise ValueError('empty_translation')
                    translated.append(output)
            separator = '' if self.target_language == 'zh' else ' '
            _append_aligned(parts, segments, span, leading + separator.join(translated) + trailing, 'text')
        output = ''.join(parts)
        if not output.strip():
            raise ValueError('empty_translation')
        return TextResult(output, self.target_language, complete=True, segments=tuple(segments))


class NllbTranslator:
    """One offline CT2 INT8 model shared for Chinese↔English translation.

    This is a research-model adapter, not an assertion of technical translation
    quality. Full input coverage and provenance are separate from semantic tests.
    """
    ADAPTER_VERSION = 'nllb-ct2-sentencepiece-alignment-v1'
    LANGUAGES = {'en': 'eng_Latn', 'zh': 'zho_Hans'}

    def __init__(self, model_path, *, threads=2, timeout_seconds=1800):
        self.model_path = Path(model_path)
        self.threads = max(1, min(int(threads), 8))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._model = self._tokenizer = self._processor_id = None
        self._lock = RLock()

    @property
    def processor_id(self):
        with self._lock:
            if self._processor_id is not None:
                return self._processor_id
            checksum = hashlib.sha256(self.ADAPTER_VERSION.encode())
            try:
                required = ('config.json', 'model.bin', 'sentencepiece.bpe.model')
                if not all((self.model_path / name).is_file() for name in required):
                    raise FileNotFoundError('model_missing')
                files = sorted(path for path in self.model_path.iterdir()
                               if path.is_file() and path.name != 'knowpipe-model.json')
                for path in files:
                    checksum.update(path.name.encode())
                    with path.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            checksum.update(chunk)
                status = 'nllb-ct2'
            except OSError:
                checksum.update(str(self.model_path.absolute()).encode())
                status = 'nllb-unavailable'
            self._processor_id = status + '-' + checksum.hexdigest()
            return self._processor_id

    def _load(self):
        if self._model is not None:
            return
        try:
            import ctranslate2
            import sentencepiece
            self._tokenizer = sentencepiece.SentencePieceProcessor(
                model_file=str(self.model_path / 'sentencepiece.bpe.model'))
            self._model = ctranslate2.Translator(str(self.model_path), device='cpu', compute_type='int8',
                                                inter_threads=1, intra_threads=self.threads)
        except Exception as error:
            self._model = self._tokenizer = None
            raise ProviderUnavailable('translation_model_unavailable') from error

    def translate(self, text, source_language, target_language):
        source = str(source_language).lower().split('-')[0]
        target = str(target_language).lower().split('-')[0]
        if source not in self.LANGUAGES or target not in self.LANGUAGES or source == target:
            raise ProviderUnavailable('translation_language_not_supported')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 4_000_000:
            raise ValueError('invalid_translation_input')
        with self._lock:
            self._load()
            deadline = time.monotonic() + self.timeout_seconds
            parts, segments = [], []
            for translate, span in translation_spans(text):
                pattern = r'[\u3400-\u9fffA-Za-z]' if source == 'zh' else r'[A-Za-z]'
                if not translate or not re.search(pattern, span):
                    _append_aligned(parts, segments, span, span, 'protected')
                    continue
                leading = span[:len(span) - len(span.lstrip())]
                trailing = span[len(span.rstrip()):]
                outputs = []
                for sentence in re.split(r'(?<=[。！？])\s*|(?<=[.!?])\s+(?=[A-Z])', span.strip()):
                    if not sentence:
                        continue
                    tokens = self._tokenizer.encode(sentence, out_type=str)
                    for offset in range(0, len(tokens), 480):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('translation_timeout')
                        batch = [[self.LANGUAGES[source], *tokens[offset:offset + 480], '</s>']]
                        result = self._model.translate_batch(batch,
                            target_prefix=[[self.LANGUAGES[target]]], beam_size=4,
                            max_input_length=0, max_decoding_length=1024, return_end_token=True)[0]
                        output_tokens = result.hypotheses[0]
                        if (len(output_tokens) < 3 or output_tokens[0] != self.LANGUAGES[target]
                                or output_tokens[-1] != '</s>'):
                            raise ValueError('translation_truncated')
                        output = self._tokenizer.decode(output_tokens[1:-1]).replace('▁', ' ').strip()
                        if not output:
                            raise ValueError('empty_translation')
                        outputs.append(output)
                output = leading + ('' if target == 'zh' else ' ').join(outputs) + trailing
                _append_aligned(parts, segments, span, output, 'text')
            return TextResult(''.join(parts), target, complete=True, segments=tuple(segments))


@lru_cache(maxsize=2)
def _shared_nllb(path, threads, timeout_seconds):
    return NllbTranslator(path, threads=threads, timeout_seconds=timeout_seconds)


def configured_language_provider(env=None):
    """Explicit shared bilingual model; factories never download weights."""
    config = os.environ if env is None else env
    llama_path = config.get('KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH')
    if llama_path:
        endpoint = config.get('KNOWPIPE_LLAMA_TRANSLATION_URL')
        if not endpoint:
            return UnconfiguredProvider()
        return _shared_llama(str(Path(llama_path).absolute()), endpoint,
                             int(config.get('KNOWPIPE_TRANSLATION_TIMEOUT_SECONDS', 1800)))
    path = config.get('KNOWPIPE_NLLB_MODEL_PATH')
    if not path:
        return UnconfiguredProvider()
    return _shared_nllb(str(Path(path).absolute()), int(config.get('KNOWPIPE_MODEL_THREADS', 2)),
                        int(config.get('KNOWPIPE_TRANSLATION_TIMEOUT_SECONDS', 1800)))


@lru_cache(maxsize=2)
def _shared_llama(path, endpoint, timeout_seconds):
    from .llama_provider import LlamaTranslator
    return LlamaTranslator(path, endpoint, timeout_seconds=timeout_seconds)


class LocalTranscriber:
    """Multilingual faster-whisper, CPU int8, local weights only."""
    def __init__(self, model_path, *, threads=2, timeout_seconds=7200):
        self.model_path = Path(model_path)
        self.threads = max(1, min(int(threads), 8))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._model = None

    def check_available(self):
        """Cheap local preflight before downloading audio; no model imports/inference."""
        required = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
        try:
            if not all((self.model_path / name).is_file() and (self.model_path / name).stat().st_size > 0
                       for name in required):
                raise FileNotFoundError('missing_model')
        except OSError as exc:
            raise ProviderUnavailable('transcription_model_unavailable') from exc

    def _load(self):
        if self._model is not None:
            return
        try:
            # faster-whisper falls back to a network tokenizer download when
            # tokenizer.json is absent, even for an otherwise local model path.
            self.check_available()
            from faster_whisper import WhisperModel
            self._model = WhisperModel(str(self.model_path), device='cpu', compute_type='int8',
                                      cpu_threads=self.threads, num_workers=1, local_files_only=True)
        except Exception as exc:
            raise ProviderUnavailable('transcription_model_unavailable') from exc

    def transcribe(self, audio_path):
        return self._transcribe(audio_path)

    def with_context(self, *, title=None, feed_title=None):
        # Only actual publisher metadata; never a reference transcript or answers.
        context = '. '.join(' '.join(value.split())[:200] for value in (feed_title, title)
                            if isinstance(value, str) and value.strip())[:400]
        return _ContextTranscriber(self, context or None)

    def _transcribe(self, audio_path, context=None):
        if not Path(audio_path).is_file():
            raise ValueError('audio_missing')
        self._load()
        deadline = time.monotonic() + self.timeout_seconds
        segments, info = self._model.transcribe(str(audio_path), beam_size=3, task='transcribe',
            condition_on_previous_text=False, temperature=0, vad_filter=True, hotwords=context)
        parts = []
        for segment in segments:
            if time.monotonic() >= deadline:
                raise TimeoutError('transcription_timeout')
            if segment.text.strip():
                parts.append(segment.text.strip())
        language = info.language
        if not isinstance(language, str) or not re.fullmatch(r'[a-z]{2,3}', language):
            raise ValueError('invalid_transcription_language')
        text = transcript_paragraphs(parts, language)
        if not text or len(text.encode('utf-8')) > 4_000_000:
            raise ValueError('invalid_transcription_output')
        return TextResult(text, language, complete=True)


class _ContextTranscriber:
    """One-call metadata context; the underlying local model is reused safely."""
    def __init__(self, provider, context):
        self.provider, self.context = provider, context

    def transcribe(self, audio_path):
        return self.provider._transcribe(audio_path, self.context)

    def check_available(self):
        self.provider.check_available()


def configured_translator(env=None):
    config = os.environ if env is None else env
    if config.get('KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH') or config.get('KNOWPIPE_NLLB_MODEL_PATH'):
        return configured_language_provider(config)
    path = config.get('KNOWPIPE_TRANSLATION_MODEL_PATH')
    if not path:
        return UnconfiguredProvider()
    return LocalTranslator(path, threads=config.get('KNOWPIPE_MODEL_THREADS', 2),
                           timeout_seconds=config.get('KNOWPIPE_TRANSLATION_TIMEOUT_SECONDS', 1800))


def configured_transcriber(env=None):
    config = os.environ if env is None else env
    path = config.get('KNOWPIPE_ASR_MODEL_PATH')
    if not path:
        return UnconfiguredProvider()
    return LocalTranscriber(path, threads=config.get('KNOWPIPE_MODEL_THREADS', 2),
                            timeout_seconds=config.get('KNOWPIPE_ASR_TIMEOUT_SECONDS', 7200))


def configured_goal_translator(env=None):
    config = os.environ if env is None else env
    if config.get('KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH') or config.get('KNOWPIPE_NLLB_MODEL_PATH'):
        return configured_language_provider(config)
    path = config.get('KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH')
    if not path:
        return UnconfiguredProvider()
    return LocalTranslator(path, source_language='zh', target_language='en',
                           threads=config.get('KNOWPIPE_MODEL_THREADS', 2),
                           timeout_seconds=config.get('KNOWPIPE_GOAL_TRANSLATION_TIMEOUT_SECONDS', 180))
