"""Full-text identity and version-bound results; ingestion success is not completeness."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from .quality import CHECKS_VERSION, public_quality, validate_segments

STAGES = ("extraction", "transcription", "translation", "analysis")
STATES = {"not_requested", "queued", "running", "ready", "failed", "unavailable", "not_required"}
PUBLIC_ERRORS = {"translation_empty_output", "translation_truncated", "translation_incomplete",
                 "translation_invalid_input", "translation_invalid_output", "translation_timeout",
                 "translation_model_unavailable", "translation_language_not_supported",
                 "translation_failed", "translation_attempts_exhausted", "provider_not_configured",
                 "translation_quality_failed", "translation_alignment_failed"}


def public_processor(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else None


def is_chinese(language):
    return isinstance(language, str) and language.lower().split("-")[0] == "zh"


def content_version(doc):
    text = doc.get("body_text")
    if not isinstance(text, str) or not text.strip():
        return None
    payload = json.dumps([doc.get("language") or "und", text], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stage(doc, name, version, default="not_requested"):
    record = (doc.get("processing") or {}).get(name) or {}
    status = record.get("status", default)
    if status not in STATES:
        status = "unverified"
    if record.get("source_version") and record["source_version"] != version:
        status = "stale"
    result = {"status": status}
    if status in {"failed", "unavailable"} and record.get("error_code") in PUBLIC_ERRORS:
        result["error_code"] = record["error_code"]
    return result


def content_view(doc, *, include_text=True):
    version = content_version(doc)
    declaration = doc.get("content") or {}
    fulltext = bool(version and declaration.get("kind") == "fulltext" and declaration.get("version") == version)
    if fulltext:
        status = "fulltext"
    elif not version:
        status = "missing"
    elif declaration.get("kind") == "fulltext":
        status = "unverified"
    else:
        status = {"arxiv": "abstract", "stackexchange": "question_only"}.get(doc.get("source"), "unverified")
    processing = {name: _stage(doc, name, version) for name in STAGES}
    processing["extraction"] = {"status": "ready" if fulltext else "unverified"}
    extraction = _stage(doc, "extraction", version)
    if not fulltext and extraction["status"] in {"failed", "queued", "running", "unavailable"}:
        processing["extraction"] = extraction
        if extraction["status"] == "failed":
            status = "failed"

    translated = doc.get("translation") or {}
    translated_text = translated.get("text")
    expected_processor = doc.get('translation_expected_processor')
    processor_matches = not expected_processor or translated.get('processor_id') == expected_processor
    stored_quality = translated.get('quality') or {}
    quality_matches = (stored_quality.get('status') != 'failed' and (not expected_processor or
                       (stored_quality.get('checks_version') == CHECKS_VERSION and
                        stored_quality.get('status') == 'checks_passed')))
    valid_translation = bool(fulltext and translated.get("source_version") == version
                             and translated.get("complete") is True and is_chinese(translated.get("language"))
                             and isinstance(translated_text, str) and translated_text.strip()
                             and processor_matches and quality_matches)
    chinese_text = doc["body_text"] if fulltext and is_chinese(doc.get("language")) else None
    if chinese_text is not None:
        processing["translation"] = {"status": "not_required"}
    elif valid_translation:
        chinese_text = translated_text
        processing["translation"] = {"status": "ready"}
    elif translated.get("source_version") and translated["source_version"] != version:
        processing["translation"] = {"status": "stale"}
    elif translated and not processor_matches and processing['translation']['status'] not in {'running', 'failed', 'unavailable'}:
        processing['translation'] = {'status': 'stale'}
    elif processing["translation"]["status"] == "ready":
        processing["translation"] = {"status": "failed"}

    # A historical batch ID alone cannot certify analysis of this text version.
    mining = doc.get("mining") or {}
    recommendation_analysis = doc.get('recommendation_analysis') or {}
    analysis = (doc.get("processing") or {}).get("analysis") or {}
    current_attempt = (analysis.get("source_version") == version and
                       analysis.get("status") in {"queued", "running", "failed", "unavailable"})
    if current_attempt:
        processing["analysis"] = {"status": analysis["status"]}
    elif fulltext and recommendation_analysis.get('corpus_id') and recommendation_analysis.get('content_version') == version:
        processing["analysis"] = {"status": "ready"}
    elif mining.get("batch_id") and fulltext and mining.get("content_version") == version:
        processing["analysis"] = {"status": "ready"}
    elif mining or processing["analysis"]["status"] == "ready":
        processing["analysis"] = {"status": "stale" if mining.get("content_version") else "unverified"}
    if processing["transcription"]["status"] == "ready" and not fulltext:
        processing["transcription"] = {"status": "unverified"}

    result = {"content_status": status, "content_version": version, "language": doc.get("language") or "und",
              "processing": processing, "chinese_ready": chinese_text is not None}
    quality_record = doc.get('translation_quality') or {}
    if (quality_record.get('source_version') == version and
            quality_record.get('processor_id') == expected_processor):
        visible_quality = quality_record
    else:
        visible_quality = stored_quality if valid_translation else None
    result['translation_quality'] = public_quality(visible_quality)
    result['translation_processor'] = public_processor(expected_processor or translated.get('processor_id'))
    if include_text:
        result.update(original_text=doc.get("body_text") or "", chinese_text=chinese_text)
        try:
            result['translation_segments'] = (validate_segments(doc['body_text'], translated_text,
                                                translated.get('segments') or []) if valid_translation else [])
        except ValueError:
            result['translation_segments'] = []
    return result


def _valid_text(text):
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4_000_000:
        raise ValueError("invalid_fulltext")


def publish_fulltext(db, source, doc_id, text, language, *, expected=None):
    """Internal ingestion boundary: caller has verified full extraction, not just a summary."""
    _valid_text(text)
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*", language):
        raise ValueError("invalid_language")
    key = {"source": source, "doc_id": doc_id, **(expected or {})}
    if db.documents.find_one(key, {"_id": 1}) is None:
        raise LookupError("not_found")
    version = content_version({"body_text": text, "language": language})
    processing = {name: {"status": "not_requested", "source_version": version} for name in STAGES}
    processing["extraction"]["status"] = "ready"
    processing["transcription"]["status"] = "not_required"
    if is_chinese(language):
        processing["translation"]["status"] = "not_required"
    db.documents.update_one({**key, "$or": [
        {"content.version": {"$ne": version}}, {"content.kind": {"$ne": "fulltext"}},
        {"body_text": {"$ne": text}}, {"language": {"$ne": language}}
    ]}, {"$set": {"body_text": text, "language": language, "content": {"kind": "fulltext", "version": version},
                  "processing": processing, "mining": None, "content_updated_at": datetime.now(timezone.utc)},
         "$unset": {"translation": "", "translation_expected_processor": "", "translation_quality": ""}})
    return db.documents.find_one(key)


def _version_filter(doc):
    return {"source": doc["source"], "doc_id": doc["doc_id"],
            "content.version": (doc.get("content") or {}).get("version"),
            "body_text": doc.get("body_text"), "language": doc.get("language")}


def set_stage_state(db, doc, stage, status, error_code=None, *, expected_processor=None, generation=None):
    """Only non-success transitions here; successful stages require artifact publishers."""
    if stage not in STAGES or status not in STATES - {"ready", "not_required"}:
        raise ValueError("invalid_processing_state")
    record = {"status": status, "source_version": content_version(doc)}
    if error_code:
        record["error_code"] = error_code
    query = _version_filter(doc)
    if expected_processor is not None:
        query['translation_expected_processor'] = expected_processor
    if stage == 'translation':
        _validate_generation(generation)
        query['translation_generation'] = generation
    return bool(db.documents.update_one(query, {"$set": {f"processing.{stage}": record}}).matched_count)


def publish_translation(db, source, doc_id, source_version, text, *, processor_id=None, quality=None,
                        generation=None, segments=()):
    _valid_text(text)
    _validate_generation(generation)
    doc = db.documents.find_one({"source": source, "doc_id": doc_id})
    if not doc or content_view(doc, include_text=False)["content_status"] != "fulltext" or content_version(doc) != source_version:
        return False
    segments = validate_segments(doc['body_text'], text, segments)
    expected = doc.get('translation_expected_processor')
    if quality is not None and (quality.get('status') != 'checks_passed' or quality.get('checks_version') != CHECKS_VERSION):
        return False
    if expected and (expected != processor_id or not quality or quality.get('status') != 'checks_passed'
                     or quality.get('checks_version') != CHECKS_VERSION):
        return False
    query = {**_version_filter(doc), 'translation_expected_processor': expected, 'translation_generation': generation}
    translation = {"text": text, "language": "zh", "complete": True, "source_version": source_version}
    if segments:
        translation['segments'] = segments
    if processor_id is not None:
        translation['processor_id'] = processor_id
    if quality is not None:
        translation['quality'] = public_quality(quality)
    return bool(db.documents.update_one(query, {"$set": {
        "translation": translation,
        "processing.translation": {"status": "ready", "source_version": source_version}
    }}).matched_count)


def _validate_generation(generation):
    if generation is not None and (type(generation) is not int or generation < 0):
        raise ValueError('invalid_translation_generation')


def claim_translation(db, doc, processor_id, *, generation=None):
    """Start one processor generation using the caller's observed document state."""
    _validate_generation(generation)
    prior_generation = doc.get('translation_generation')
    expected_processor = doc.get('translation_expected_processor')
    if prior_generation is not None:
        if generation is None or generation < prior_generation:
            return False
        if generation == prior_generation and expected_processor and expected_processor != processor_id:
            return False
    query = {**_version_filter(doc), 'translation_expected_processor': expected_processor,
             'translation_generation': prior_generation}
    update = {
        'translation_expected_processor': processor_id,
        'processing.translation': {'status': 'running', 'source_version': content_version(doc)},
    }
    if generation is not None:
        update['translation_generation'] = generation
    return bool(db.documents.update_one(query, {'$set': update, '$unset': {'translation_quality': ''}}).matched_count)


def publish_translation_quality(db, doc, processor_id, quality, *, generation=None):
    _validate_generation(generation)
    record = {**public_quality(quality), 'source_version': content_version(doc), 'processor_id': processor_id}
    query = {**_version_filter(doc), 'translation_expected_processor': processor_id, 'translation_generation': generation}
    return bool(db.documents.update_one(query, {'$set': {'translation_quality': record}}).matched_count)
