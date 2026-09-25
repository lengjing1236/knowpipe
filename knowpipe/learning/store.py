"""Personal learning state and bounded document reads backed by MongoDB."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..web import mongo_sink
from .content import content_version, content_view


class ContentChangedError(Exception):
    """The displayed version no longer matches the document being marked."""


def ensure_indexes(db):
    # Match the ingestion index options so opening Web after a mining job is safe.
    db.documents.create_index([("source", 1), ("doc_id", 1)], unique=True)


def profile_view(db, user_id):
    profile = mongo_sink.get_or_create_profile(db, user_id)
    return {"goal": profile.get("learning_goal"), "revision": profile.get("learning_revision", 0),
            "read_count": len(profile.get("read_doc_ids", []))}


def save_goal(db, user_id, text):
    mongo_sink.get_or_create_profile(db, user_id)
    now = datetime.now(timezone.utc)
    db.user_profiles.update_one({"user_id": user_id, "learning_goal.text": {"$ne": text}}, {
        "$set": {"learning_goal": {"text": text, "updated_at": now}, "updated_at": now},
        "$inc": {"learning_revision": 1}})
    return profile_view(db, user_id)


def mark_read(db, user_id, source, doc_id, read, expected_version=None):
    ref = {"source": source, "doc_id": doc_id}
    doc = db.documents.find_one(ref) if read else None
    if read and doc is None:
        raise LookupError("not_found")
    if read and content_version(doc) != expected_version:
        raise ContentChangedError("content_changed")
    mongo_sink.get_or_create_profile(db, user_id)
    now = datetime.now(timezone.utc)
    query = {"user_id": user_id}
    update = {"$inc": {"learning_revision": 1}, "$set": {"updated_at": now}}
    if read:
        query["read_doc_ids"] = {"$not": {"$elemMatch": ref}}
        update["$push"] = {"read_doc_ids": {**ref, "read_at": now, "content_version": expected_version}}
    else:
        query["read_doc_ids"] = {"$elemMatch": ref}
        update["$pull"] = {"read_doc_ids": ref}
    db.user_profiles.update_one(query, update)
    profile = mongo_sink.get_or_create_profile(db, user_id)
    actual = any(r.get("source") == source and r.get("doc_id") == doc_id for r in profile.get("read_doc_ids", []))
    return {**ref, "is_read": actual, "revision": profile.get("learning_revision", 0)}


def _references(db, user_id):
    return mongo_sink.get_or_create_profile(db, user_id).get("read_doc_ids", [])


def _version_status(ref, doc):
    if doc is None:
        return "unavailable"
    if not ref.get("content_version") or not content_version(doc):
        return "unknown"
    return "current" if ref["content_version"] == content_version(doc) else "changed"


def _document_item(doc, ref=None, *, detail=False):
    result = {field: doc.get(field) for field in ("source", "doc_id", "title", "source_url")}
    if detail:
        result.update({field: doc.get(field) for field in ('license', 'authors', 'provenance', 'source_site', 'document_type', 'quality', 'fulltext_provenance')})
    result.update(content_view(doc, include_text=detail))
    result.update(is_read=ref is not None, read_version_status=_version_status(ref, doc) if ref else None)
    return result


def document_detail(db, user_id, source, doc_id):
    doc = db.documents.find_one({"source": source, "doc_id": doc_id})
    if doc is None:
        raise LookupError("not_found")
    ref = next((r for r in _references(db, user_id) if r.get("source") == source and r.get("doc_id") == doc_id), None)
    return _document_item(doc, ref, detail=True)


def list_documents(db, user_id, *, page=1, limit=20, source=None, q=""):
    query = {}
    if source:
        query["source"] = source
    if q:
        query["title"] = {"$regex": re.escape(q), "$options": "i"}
    refs = {(r.get("source"), r.get("doc_id")): r for r in _references(db, user_id)}
    # Only the requested page is hydrated; large originals never enter a list response.
    docs = db.documents.find(query, {"body_raw": 0}).sort([("source", 1), ("doc_id", 1)]).skip((page - 1) * limit).limit(limit)
    return {"items": [_document_item(doc, refs.get((doc["source"], doc["doc_id"]))) for doc in docs],
            "total": db.documents.count_documents(query), "page": page, "limit": limit,
            "sources": sorted(s for s in db.documents.distinct("source") if isinstance(s, str))}


def list_history(db, user_id, *, page=1, limit=20):
    refs = list(reversed(_references(db, user_id)))
    selected = refs[(page - 1) * limit:page * limit]
    keys = [{"source": ref["source"], "doc_id": ref["doc_id"]} for ref in selected]
    docs = {(d["source"], d["doc_id"]): d for d in db.documents.find({"$or": keys}, {"body_raw": 0})} if keys else {}
    items = []
    for ref in selected:
        doc = docs.get((ref["source"], ref["doc_id"]))
        item = _document_item(doc, ref) if doc else {"source": ref["source"], "doc_id": ref["doc_id"],
                                                    "title": "资料已不可用", "is_read": True, "content_status": "missing"}
        item.update(read_at=ref.get("read_at"), read_content_version=ref.get("content_version"),
                    version_status=_version_status(ref, doc))
        items.append(item)
    return {"items": items, "total": len(refs), "page": page, "limit": limit}
