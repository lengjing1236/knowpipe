"""Replaceable provider contracts. No network service is configured implicitly."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Protocol

from . import content
from .quality import CHECKS_VERSION, check_translation, validate_segments


@dataclass(frozen=True)
class TextResult:
    text: str
    language: str
    complete: bool = True
    segments: tuple = ()


class ProviderUnavailable(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> TextResult: ...


class Translator(Protocol):
    def translate(self, text: str, source_language: str, target_language: str) -> TextResult: ...


class UnconfiguredProvider:
    processor_id = 'translation-not-configured-v1'

    def transcribe(self, audio_path):
        raise ProviderUnavailable("transcription_not_configured")

    def translate(self, text, source_language, target_language):
        raise ProviderUnavailable("translation_not_configured")


_TRANSLATION_ERROR_CODES = {
    (ValueError, "empty_translation"): "translation_empty_output",
    (ValueError, "translation_truncated"): "translation_truncated",
    (ValueError, "incomplete_translation"): "translation_incomplete",
    (ValueError, "invalid_translation_input"): "translation_invalid_input",
    (ValueError, "invalid_fulltext"): "translation_invalid_output",
    (ValueError, "translation_quality_failed"): "translation_quality_failed",
    (ValueError, "invalid_translation_alignment"): "translation_alignment_failed",
    (ValueError, "invalid_translation_output"): "translation_invalid_output",
    (ValueError, "translation_paragraph_too_long"): "translation_paragraph_too_long",
    (ValueError, "translation_model_mismatch"): "translation_model_mismatch",
    (ValueError, "translation_protected_code_damaged"): "translation_protected_code_damaged",
    (TimeoutError, "translation_timeout"): "translation_timeout",
    (ProviderUnavailable, "translation_model_unavailable"): "translation_model_unavailable",
    (ProviderUnavailable, "translation_language_not_supported"): "translation_language_not_supported",
    (ProviderUnavailable, "translation_rate_limited"): "translation_rate_limited",
    (ProviderUnavailable, "translation_remote_unavailable"): "translation_remote_unavailable",
}


def _translation_error_code(error, fallback):
    """Expose only exact, local error identifiers, never arbitrary provider messages."""
    if len(error.args) != 1 or not isinstance(error.args[0], str):
        return fallback
    return _TRANSLATION_ERROR_CODES.get((type(error), error.args[0]), fallback)


def processor_identity(provider):
    """Stable identity without serializing provider internals, model paths or keys.

    LocalTranslator fingerprints its model. Custom providers should expose a
    versioned processor_id; the class-only fallback supports legacy test adapters.
    """
    identity = getattr(provider, 'processor_id', None)
    if not isinstance(identity, str) or not identity:
        identity = f'{type(provider).__module__}.{type(provider).__qualname__}'
    safe = content.public_processor(identity)
    if safe:
        return safe
    return 'provider-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def translation_matches(doc, provider):
    view = content.content_view(doc, include_text=False)
    if not view['chinese_ready']:
        return False
    if content.is_chinese(doc.get('language')):
        return True
    translation = doc.get('translation') or {}
    quality = translation.get('quality') or {}
    return (translation.get('processor_id') == processor_identity(provider)
            and quality.get('checks_version') == CHECKS_VERSION and quality.get('status') == 'checks_passed')


def translate_document(db, source, doc_id, provider: Translator, *, generation=None):
    """Worker entrypoint; cache full results and reject results from superseded text."""
    key = {"source": source, "doc_id": doc_id}
    doc = db.documents.find_one(key)
    if doc is None:
        raise LookupError("not_found")
    view = content.content_view(doc)
    if view["content_status"] != "fulltext":
        raise ValueError("fulltext_required")
    if translation_matches(doc, provider):
        return view
    processor_id = processor_identity(provider)
    if not content.claim_translation(db, doc, processor_id, generation=generation):
        return content.content_view(db.documents.find_one(key) or {})
    try:
        result = provider.translate(doc["body_text"], doc["language"], "zh")
        if not isinstance(result, TextResult) or result.complete is not True or not content.is_chinese(result.language):
            raise ValueError("incomplete_translation")
        content._valid_text(result.text)
        segments = validate_segments(doc['body_text'], result.text, result.segments)
        quality = check_translation(doc['body_text'], result.text)
        if not content.publish_translation_quality(db, doc, processor_id, quality, generation=generation):
            return content.content_view(db.documents.find_one(key) or {})
        if quality['status'] != 'checks_passed':
            raise ValueError('translation_quality_failed')
        content.publish_translation(db, source, doc_id, view["content_version"], result.text,
                                    processor_id=processor_id, quality=quality, generation=generation,
                                    segments=segments)
    except ProviderUnavailable as error:
        content.set_stage_state(db, doc, "translation", "unavailable",
                                _translation_error_code(error, "provider_not_configured"), expected_processor=processor_id,
                                generation=generation)
    except Exception as error:
        # Provider exceptions can contain remote URLs or credentials; do not expose them.
        content.set_stage_state(db, doc, "translation", "failed",
                                _translation_error_code(error, "translation_failed"), expected_processor=processor_id,
                                generation=generation)
    return content.content_view(db.documents.find_one(key) or {})
