"""Session-owned learning APIs. Document IDs remain data, never URL path fragments."""
from __future__ import annotations

import re

from flask import Blueprint, current_app, jsonify, request

from . import auth
from ..learning import store
from ..recommendations import queue

learning_bp = Blueprint("learning", __name__, url_prefix="/api/learning")


def _db():
    return current_app.config["MONGO_DB"]


@learning_bp.before_request
@auth.login_required(_db)
def check_identity():
    if "user_id" in request.args or (isinstance(request.get_json(silent=True), dict) and "user_id" in request.json):
        return jsonify(error="unexpected_user_id"), 400


@learning_bp.errorhandler(ValueError)
def invalid_input(error):
    return jsonify(error="invalid_learning_input"), 400


@learning_bp.errorhandler(LookupError)
def not_found(error):
    return jsonify(error="not_found"), 404


@learning_bp.errorhandler(store.ContentChangedError)
def changed_content(error):
    return jsonify(error="content_changed"), 409


def _identity(data):
    source, doc_id = data.get("source"), data.get("doc_id")
    if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source):
        raise ValueError("invalid_source")
    if not isinstance(doc_id, str) or not doc_id.strip() or len(doc_id) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in doc_id):
        raise ValueError("invalid_doc_id")
    return source, doc_id


def _pagination():
    page, limit = int(request.args.get("page", "1")), int(request.args.get("limit", "20"))
    if not 1 <= page <= 100000 or not 1 <= limit <= 50:
        raise ValueError("invalid_pagination")
    return page, limit


@learning_bp.get("/profile")
def profile():
    return jsonify(store.profile_view(_db(), auth.current_user_id()))


@learning_bp.put("/goal")
def goal():
    body = request.get_json()
    text = body.get("text")
    if set(body) != {"text"} or not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
        raise ValueError("invalid_goal")
    return jsonify(store.save_goal(_db(), auth.current_user_id(), text.strip()))


@learning_bp.put("/read-state")
def read_state():
    body = request.get_json()
    required = {"source", "doc_id", "read"}
    if body.get("read") is True:
        required.add("content_version")
    if set(body) != required or not isinstance(body.get("read"), bool):
        raise ValueError("invalid_read_state")
    version = body.get("content_version")
    if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version)):
        raise ValueError("invalid_content_version")
    source, doc_id = _identity(body)
    return jsonify(store.mark_read(_db(), auth.current_user_id(), source, doc_id, body["read"], version))


@learning_bp.get("/document")
def document():
    source, doc_id = _identity(request.args)
    return jsonify(store.document_detail(_db(), auth.current_user_id(), source, doc_id))


@learning_bp.get("/documents")
def documents():
    page, limit = _pagination()
    source = request.args.get("source") or None
    if source and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source):
        raise ValueError("invalid_source")
    q = request.args.get("q", "").strip()
    if len(q) > 100:
        raise ValueError("invalid_query")
    return jsonify(store.list_documents(_db(), auth.current_user_id(), page=page, limit=limit, source=source, q=q))


@learning_bp.get("/history")
def history():
    page, limit = _pagination()
    return jsonify(store.list_history(_db(), auth.current_user_id(), page=page, limit=limit))


@learning_bp.get('/recommendations')
def recommendations():
    return jsonify(queue.view(_db(), auth.current_user_id()))


@learning_bp.post('/recommendations')
def refresh_recommendations():
    if request.get_json() != {}:
        raise ValueError('unexpected_recommendation_input')
    return jsonify(queue.request_refresh(_db(), auth.current_user_id())), 202
