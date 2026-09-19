"""非个性化基线排序：纯相似度排序，无已读文档时退化为最新优先（research.md §4）。"""
from __future__ import annotations

from typing import Any

from . import mongo_sink


def _latest_mining_result(db: Any, source: str, doc_id: str) -> dict[str, Any] | None:
    doc = db.documents.find_one({"source": source, "doc_id": doc_id})
    if doc is None or not doc.get("mining"):
        return None
    return db.mining_results.find_one(
        {"doc_id": doc_id, "source": source, "batch_id": doc["mining"]["batch_id"]})


def get_baseline_recommendations(db: Any, read_doc_ids: list[dict[str, str]], limit: int = 20,
                                  source: str | None = None) -> list[dict[str, Any]]:
    """用户有已读文档时，取其 similar_doc_ids 关联文档按相似度顺序展示；
    否则按 created_at 倒序展示最新文档。两种情况都不读取用户已知主题集合。
    """
    if read_doc_ids:
        seen = {(ref.get("source"), ref.get("doc_id")) for ref in read_doc_ids}
        similar_ids: list[tuple[str, str]] = []
        for ref in read_doc_ids:
            mr = _latest_mining_result(db, ref.get("source"), ref.get("doc_id"))
            if mr is None:
                continue
            for sim_doc_id in mr.get("similar_doc_ids", []):
                key = (ref.get("source"), sim_doc_id)
                if key not in seen:
                    seen.add(key)
                    similar_ids.append(key)

        items = []
        for src, doc_id in similar_ids:
            if source and src != source:
                continue
            display = mongo_sink.get_document_display(db, src, doc_id)
            if display is not None:
                items.append({"document": display})
            if len(items) >= limit:
                break
        return items

    query: dict[str, Any] = {}
    if source:
        query["source"] = source
    docs = db.documents.find(query).sort([("created_at", -1), ("source", 1), ("doc_id", 1)]).limit(limit)
    return [{"document": {
        "source": doc["source"], "doc_id": doc["doc_id"],
        "title": doc.get("title"), "source_url": doc.get("source_url"),
    }} for doc in docs]
