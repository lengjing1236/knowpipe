"""/api/* 路由处理函数（contracts/api-contract.md）。"""
from __future__ import annotations

import logging
import uuid
from datetime import timezone

from flask import Blueprint, current_app, jsonify, request

from . import auth, baseline, classify, mongo_sink
from .security import csrf_token

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

MIN_PASSWORD_LENGTH = 8
VALID_SOURCES = ("stackexchange", "arxiv")
VALID_MODES = ("personalized", "baseline")
VALID_FEEDBACK_ACTIONS = ("confirmed_known", "useful", "irrelevant")


def _db():
    return current_app.config["MONGO_DB"]


def _login_required(view_func):
    return auth.login_required(_db)(view_func)


@api_bp.post("/auth/register")
def register():
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        return jsonify({"error": "missing_fields"}), 400
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 256 or not username.strip():
        return jsonify({"error": "invalid_credentials_format"}), 400
    username = username.strip()
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": "password_too_short"}), 400

    db = _db()
    user_id = uuid.uuid4().hex
    try:
        mongo_sink.create_user(db, user_id, username, auth.hash_password(password))
    except mongo_sink.UsernameTakenError:
        logger.info("注册失败：用户名已存在 username=%s", username)
        return jsonify({"error": "username_taken"}), 409
    return jsonify({"user_id": user_id, "username": username}), 201


@api_bp.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(username) <= 64 or len(password) > 256:
        return jsonify({"error": "invalid_credentials_format"}), 400
    username = username.strip()
    db = _db()
    user = mongo_sink.find_user_by_username(db, username) if username else None
    if user is None or not password or not auth.verify_password(password, user["password_hash"]):
        logger.info("登录失败：用户名或密码不正确 username=%s", username)
        return jsonify({"error": "invalid_credentials"}), 401
    auth.login_user(user["user_id"])
    return jsonify({"user_id": user["user_id"], "username": user["username"]}), 200


@api_bp.post("/auth/logout")
def logout():
    @_login_required
    def _handle():
        auth.logout_user()
        return jsonify({}), 200
    return _handle()


@api_bp.get("/topics")
def list_topics():
    aggregations = mongo_sink.get_topic_aggregations(_db())
    return jsonify({"topics": [
        {"topic_cluster_id": a["topic_cluster_id"], "top_keywords": a["top_keywords"],
         "document_count": a["document_count"]}
        for a in aggregations
    ]}), 200


@api_bp.get("/documents/<source>/<doc_id>")
def document_detail(source: str, doc_id: str):
    detail = mongo_sink.get_document_detail(_db(), source, doc_id)
    if detail is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(detail), 200


def _reclassify_user(db, user_id: str) -> None:
    """按当前画像重新判定该用户名下全部知识单元（research.md §5，同步完成）。"""
    profile = mongo_sink.get_or_create_profile(db, user_id)
    units = mongo_sink.get_knowledge_units(db)
    classified = classify.classify_all(units, profile.get("known_keywords", []),
                                        profile.get("known_topics", []))
    mongo_sink.upsert_user_knowledge_status(db, user_id, classified)


@api_bp.get("/recommendations")
def recommendations():
    @_login_required
    def _handle():
        db = _db()
        requested_user_id = request.args.get("user_id")
        if not requested_user_id or not auth.check_owns_resource(requested_user_id):
            return auth.forbidden_response()

        limit_raw = request.args.get("limit", "20")
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = -1
        if not 1 <= limit <= 100:
            return jsonify({"error": "invalid_limit"}), 400

        source = request.args.get("source")
        if source is not None and source not in VALID_SOURCES:
            return jsonify({"error": "invalid_source"}), 400

        mode = request.args.get("mode", "personalized")
        if mode not in VALID_MODES:
            return jsonify({"error": "invalid_mode"}), 400

        if mode == "baseline":
            profile = mongo_sink.get_or_create_profile(db, requested_user_id)
            items = baseline.get_baseline_recommendations(
                db, profile.get("read_doc_ids", []), limit=limit, source=source)
            return jsonify({"user_id": requested_user_id, "mode": mode, "items": items}), 200

        if not mongo_sink.has_user_knowledge(db, requested_user_id):
            _reclassify_user(db, requested_user_id)
        records = mongo_sink.get_user_knowledge(db, requested_user_id, limit=limit, source=source)
        items = []
        for record in records:
            items.append({
                "knowledge_id": record["knowledge_id"],
                "status": record["status"],
                "matched_keywords": record.get("matched_keywords", []),
                "matched_topic_cluster_id": record.get("matched_topic_cluster_id"),
                "score": record.get("score", 0.0),
                "document": mongo_sink.get_document_display(db, record.get("source"), record.get("doc_id")),
            })
        return jsonify({"user_id": requested_user_id, "mode": mode, "items": items}), 200
    return _handle()


@api_bp.post("/profile/topics")
def update_topics():
    @_login_required
    def _handle():
        body = request.get_json(silent=True) or {}
        requested_user_id = body.get("user_id")
        if not requested_user_id or not auth.check_owns_resource(requested_user_id):
            return auth.forbidden_response()

        known_topics = body.get("known_topics")
        if not isinstance(known_topics, list):
            return jsonify({"error": "invalid_known_topics"}), 400

        db = _db()
        valid_topic_ids = {str(a["topic_cluster_id"]) for a in mongo_sink.get_topic_aggregations(db)}
        if any(str(t) not in valid_topic_ids for t in known_topics):
            return jsonify({"error": "invalid_known_topics"}), 400

        mongo_sink.get_or_create_profile(db, requested_user_id)
        # Reading is a declaration of exposure, not evidence of mastery.
        known_keywords = mongo_sink.derive_known_keywords(
            db, known_topics, [])
        result = mongo_sink.save_known_topics(db, requested_user_id, known_topics, known_keywords)
        _reclassify_user(db, requested_user_id)
        return jsonify({"known_topics": result["known_topics"],
                        "updated_at": result["updated_at"].isoformat()}), 200
    return _handle()


@api_bp.post("/profile/feedback")
def submit_feedback():
    @_login_required
    def _handle():
        body = request.get_json(silent=True) or {}
        requested_user_id = body.get("user_id")
        if not requested_user_id or not auth.check_owns_resource(requested_user_id):
            return auth.forbidden_response()

        knowledge_id = body.get("knowledge_id")
        action = body.get("action")
        if not isinstance(knowledge_id, str) or not knowledge_id or len(knowledge_id) > 256 or action not in VALID_FEEDBACK_ACTIONS:
            return jsonify({"error": "invalid_action"}), 400

        db = _db()
        entry = mongo_sink.append_feedback(db, requested_user_id, knowledge_id, action)

        if action == "confirmed_known":
            profile = mongo_sink.get_or_create_profile(db, requested_user_id)
            known_keywords = set(profile.get("known_keywords", []))
            known_topics = set(str(t) for t in profile.get("known_topics", []))
            if knowledge_id.startswith("kw:"):
                known_keywords.add(knowledge_id[len("kw:"):])
            elif knowledge_id.startswith("topic:"):
                known_topics.add(knowledge_id[len("topic:"):])
            mongo_sink.save_profile_known_state(
                db, requested_user_id, sorted(known_topics), sorted(known_keywords))
            _reclassify_user(db, requested_user_id)

        return jsonify({"knowledge_id": knowledge_id, "action": action,
                        "created_at": entry["created_at"].isoformat()}), 200
    return _handle()


@api_bp.get("/auth/csrf")
def get_csrf():
    return jsonify(csrf_token=csrf_token())


@api_bp.get("/auth/me")
@_login_required
def me():
    user = mongo_sink.find_user_by_id(_db(), auth.current_user_id())
    profile = mongo_sink.get_or_create_profile(_db(), user["user_id"])
    return jsonify(user_id=user["user_id"], username=user["username"],
                   known_topics=profile.get("known_topics", []))


@api_bp.get("/stats")
def stats():
    db = _db()
    sources = {row['_id']: row['count'] for row in db.documents.aggregate([
        {'$group': {'_id': '$source', 'count': {'$sum': 1}}}]) if row['_id']}
    fields = {name: 1 for name in ('batch_id', 'status', 'sources', 'input_count',
              'valid_count', 'failed_count', 'started_at', 'finished_at',
              'spark_application_id', 'spark_master', 'segment_count')}
    fields['_id'] = 0
    batches = list(db.batches.find({}, fields).sort('started_at', -1).limit(5))
    for batch in batches:
        for name in ('started_at', 'finished_at'):
            if hasattr(batch.get(name), 'isoformat'):
                batch[name] = batch[name].replace(tzinfo=timezone.utc).isoformat()
    return jsonify(documents=sum(sources.values()), sources=sources, batches=batches,
                   podcast_episodes=db.podcast_episodes.count_documents({'status': 'ready'}))
