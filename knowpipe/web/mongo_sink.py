"""MongoDB 读写层：users/user_profiles/user_knowledge 的读写与索引创建；
只读查询 Feature 1 的 documents/mining_results（不修改其结构，见 storage-contract）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError


class UsernameTakenError(Exception):
    """用户名已存在，对应 Contract 1 的 409。"""


def ensure_indexes(db: Any) -> None:
    """创建 data-model.md 中定义的 users/user_profiles/user_knowledge 索引。"""
    db.users.create_index("user_id", unique=True)
    db.users.create_index("username", unique=True)
    db.user_profiles.create_index("user_id", unique=True)
    db.user_knowledge.create_index([("user_id", 1), ("knowledge_id", 1)], unique=True)
    db.batches.create_index("batch_id", unique=True)


# ---- users ----

def create_user(db: Any, user_id: str, username: str, password_hash: str) -> dict[str, Any]:
    if db.users.find_one({"username": username}) is not None:
        raise UsernameTakenError(username)
    record = {
        "user_id": user_id,
        "username": username,
        "password_hash": password_hash,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        db.users.insert_one(dict(record))
    except DuplicateKeyError as exc:
        raise UsernameTakenError(username) from exc
    return record


def find_user_by_username(db: Any, username: str) -> dict[str, Any] | None:
    return db.users.find_one({"username": username})


def find_user_by_id(db: Any, user_id: str) -> dict[str, Any] | None:
    return db.users.find_one({"user_id": user_id})


# ---- user_profiles ----

def get_or_create_profile(db: Any, user_id: str) -> dict[str, Any]:
    profile = db.user_profiles.find_one({"user_id": user_id})
    if profile is not None:
        return profile
    profile = {
        "user_id": user_id,
        "known_topics": [],
        "known_keywords": [],
        "read_doc_ids": [],
        "feedback_history": [],
        "updated_at": datetime.now(timezone.utc),
    }
    try:
        db.user_profiles.update_one({"user_id": user_id}, {"$setOnInsert": profile}, upsert=True)
    except DuplicateKeyError:
        # Another request initialized the same profile between our read and upsert.
        if db.user_profiles.find_one({"user_id": user_id}) is None:
            raise
    return db.user_profiles.find_one({"user_id": user_id})


def save_known_topics(db: Any, user_id: str, known_topics: list[str],
                       known_keywords: list[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    db.user_profiles.update_one(
        {"user_id": user_id},
        {"$set": {"known_topics": known_topics, "known_keywords": known_keywords,
                   "updated_at": now}},
        upsert=True,
    )
    return {"known_topics": known_topics, "updated_at": now}


def append_feedback(db: Any, user_id: str, knowledge_id: str, action: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    entry = {"knowledge_id": knowledge_id, "action": action, "created_at": now}
    db.user_profiles.update_one(
        {"user_id": user_id},
        {"$push": {"feedback_history": entry}, "$set": {"updated_at": now}},
        upsert=True,
    )
    return entry


def save_profile_known_state(db: Any, user_id: str, known_topics: list[str],
                              known_keywords: list[str]) -> None:
    """覆盖写入画像的 known_topics/known_keywords（供 confirmed_known 反馈更新调用）。"""
    now = datetime.now(timezone.utc)
    db.user_profiles.update_one(
        {"user_id": user_id},
        {"$set": {"known_topics": known_topics, "known_keywords": known_keywords,
                   "updated_at": now}},
        upsert=True,
    )


# ---- 只读查询 Feature 1 的 documents/mining_results ----

def _iter_latest_mining_results(db: Any):
    """按 documents.mining.batch_id（Feature 1 mongo_sink 写入时始终指向最新批次）
    定位每篇文档当前有效的 mining_results 记录，避免误读历史批次。"""
    pending = []
    for doc in db.documents.find({"mining": {"$ne": None}}):
        if (doc.get("mining") or {}).get("batch_id"):
            pending.append(doc)
        if len(pending) >= 500:
            yield from _load_result_chunk(db, pending)
            pending = []
    if pending:
        yield from _load_result_chunk(db, pending)


def _load_result_chunk(db, documents):
    keys = [{"source": doc["source"], "doc_id": doc["doc_id"],
             "batch_id": doc["mining"]["batch_id"]} for doc in documents]
    results = {(row["source"], row["doc_id"], row["batch_id"]): row
               for row in db.mining_results.find({"$or": keys})}
    for doc in documents:
        result = results.get((doc["source"], doc["doc_id"], doc["mining"]["batch_id"]))
        if result is not None:
            yield doc, result


def get_topic_aggregations(db: Any, top_keywords_n: int = 5) -> list[dict[str, Any]]:
    """按主题簇聚合代表关键词与文档数（供 /api/topics 与知识单元枚举使用）。

    data-model.md Assumptions 未固定聚合方式，取簇内 TF-IDF 权重最高的若干关键词
    作为默认实现（对应 tasks.md T026）。
    """
    clusters: dict[int, dict[str, Any]] = {}
    for doc, mr in _iter_latest_mining_results(db):
        tcid = mr["topic_cluster_id"]
        entry = clusters.setdefault(tcid, {"keyword_weights": {}, "docs_seen": set()})
        entry["docs_seen"].add((doc["source"], doc["doc_id"]))
        for kw in mr.get("keywords", []):
            term, weight = kw["term"], kw["weight"]
            cur = entry["keyword_weights"].get(term)
            if cur is None or weight > cur[0]:
                entry["keyword_weights"][term] = (weight, doc["source"], doc["doc_id"])

    aggregations = []
    for tcid, entry in clusters.items():
        sorted_terms = sorted(entry["keyword_weights"].items(), key=lambda kv: -kv[1][0])
        top_terms = sorted_terms[:top_keywords_n]
        if top_terms:
            _, rep_source, rep_doc_id = top_terms[0][1]
        else:
            rep_source, rep_doc_id = next(iter(entry["docs_seen"]), (None, None))
        aggregations.append({
            "topic_cluster_id": tcid,
            "top_keywords": [term for term, _ in top_terms],
            "top_keyword_details": [
                {"term": term, "weight": weight, "source": src, "doc_id": did}
                for term, (weight, src, did) in top_terms
            ],
            "document_count": len(entry["docs_seen"]),
            "representative_source": rep_source,
            "representative_doc_id": rep_doc_id,
        })
    aggregations.sort(key=lambda a: -a["document_count"])
    return aggregations


def get_knowledge_units(db: Any, top_keywords_n: int = 5) -> list[dict[str, Any]]:
    """枚举本 feature 判定所依据的知识单元：每个主题簇一个 topic 单元，
    加上该簇代表关键词各一个 kw 单元（对应 data-model.md knowledge_id 格式）。"""
    units = []
    for agg in get_topic_aggregations(db, top_keywords_n=top_keywords_n):
        tcid = agg["topic_cluster_id"]
        units.append({
            "knowledge_id": f"topic:{tcid}",
            "keywords": list(agg["top_keywords"]),
            "topic_cluster_id": tcid,
            "source": agg["representative_source"],
            "doc_id": agg["representative_doc_id"],
        })
        for detail in agg["top_keyword_details"]:
            units.append({
                "knowledge_id": f"kw:{detail['term']}",
                "keywords": [detail["term"]],
                "topic_cluster_id": tcid,
                "source": detail["source"],
                "doc_id": detail["doc_id"],
            })
    return units


def derive_known_keywords(db: Any, known_topics: list[str],
                           read_doc_ids: list[dict[str, str]]) -> list[str]:
    """从已知主题簇与已读文档的挖掘结果中聚合已知关键词（data-model.md UserProfile 派生规则）。"""
    topic_aggs = {str(a["topic_cluster_id"]): a for a in get_topic_aggregations(db)}
    keywords: set[str] = set()
    for topic_id in known_topics:
        agg = topic_aggs.get(str(topic_id))
        if agg is not None:
            keywords.update(agg["top_keywords"])
    for ref in read_doc_ids:
        doc = db.documents.find_one({"source": ref.get("source"), "doc_id": ref.get("doc_id")})
        if doc is None or not doc.get("mining"):
            continue
        batch_id = doc["mining"].get("batch_id")
        mr = db.mining_results.find_one(
            {"doc_id": doc["doc_id"], "source": doc["source"], "batch_id": batch_id})
        if mr is not None:
            keywords.update(kw["term"] for kw in mr.get("keywords", []))
    return sorted(keywords)


def get_document_detail(db: Any, source: str, doc_id: str) -> dict[str, Any] | None:
    doc = db.documents.find_one({"source": source, "doc_id": doc_id})
    if doc is None:
        return None
    mining_summary = None
    if doc.get("mining"):
        mr = db.mining_results.find_one(
            {"doc_id": doc_id, "source": source, "batch_id": doc["mining"]["batch_id"]})
        if mr is not None:
            mining_summary = {
                "keywords": mr.get("keywords", []),
                "topic_cluster_id": mr.get("topic_cluster_id"),
                "reliable": mr.get("reliable", True),
            }
    return {
        "source": doc["source"],
        "doc_id": doc["doc_id"],
        "title": doc.get("title"),
        "body_text": doc.get("body_text"),
        "source_url": doc.get("source_url"),
        "license": doc.get("license"),
        "mining": mining_summary,
    }


def get_document_display(db: Any, source: str | None, doc_id: str | None) -> dict[str, Any] | None:
    """供 /api/recommendations 组装 items[].document 展示字段用，比 get_document_detail 更精简。"""
    if not source or not doc_id:
        return None
    doc = db.documents.find_one({"source": source, "doc_id": doc_id})
    if doc is None:
        return None
    return {
        "source": doc["source"],
        "doc_id": doc["doc_id"],
        "title": doc.get("title"),
        "source_url": doc.get("source_url"),
    }


# ---- user_knowledge ----

def upsert_user_knowledge_status(db: Any, user_id: str, records: list[dict[str, Any]]) -> None:
    """批量写入判定结果（status/matched_*），不覆盖 score/batch_id（由 score_job.py 单独写入）。"""
    now = datetime.now(timezone.utc)
    for record in records:
        db.user_knowledge.update_one(
            {"user_id": user_id, "knowledge_id": record["knowledge_id"]},
            {
                "$set": {
                    "source": record.get("source"),
                    "doc_id": record.get("doc_id"),
                    "status": record["status"],
                    "matched_keywords": record.get("matched_keywords", []),
                    "matched_topic_cluster_id": record.get("matched_topic_cluster_id"),
                    "conflict_evidence": record.get("conflict_evidence"),
                    "pending_review": record.get("pending_review", False),
                    "updated_at": now,
                },
                "$setOnInsert": {"score": 0.0, "batch_id": ""},
            },
            upsert=True,
        )


def has_user_knowledge(db: Any, user_id: str) -> bool:
    return db.user_knowledge.count_documents({"user_id": user_id}) > 0


def get_user_knowledge(db: Any, user_id: str, limit: int = 20,
                        source: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"user_id": user_id}
    if source:
        query["source"] = source
    return list(db.user_knowledge.find(query).sort([("score", -1), ("knowledge_id", 1)]).limit(limit))


def write_score_batch(db: Any, entries: list[dict[str, Any]], batch_id: str) -> None:
    """写入 score_job.py 计算出的 (user_id, knowledge_id) -> score，供 user_knowledge.score 更新。"""
    now = datetime.now(timezone.utc)
    for entry in entries:
        db.user_knowledge.update_one(
            {"user_id": entry["user_id"], "knowledge_id": entry["knowledge_id"]},
            {"$set": {"score": entry["score"], "batch_id": batch_id, "updated_at": now}},
            upsert=True,
        )
