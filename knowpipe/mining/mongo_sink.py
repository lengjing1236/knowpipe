"""MongoDB 写入层：upsert documents、写入 mining_results、创建索引。"""
from __future__ import annotations

from typing import Any


def ensure_indexes(db: Any) -> None:
    """在 documents/mining_results/batches 上创建 data-model.md 中定义的索引。"""
    db.documents.create_index([("source", 1), ("doc_id", 1)], unique=True)
    db.mining_results.create_index([("doc_id", 1), ("batch_id", 1)])
    db.batches.create_index("batch_id", unique=True)


def upsert_documents(db: Any, records: list[dict[str, Any]]) -> None:
    """按 source+doc_id 做 upsert 覆盖写入 documents 集合（对应 spec FR-012 幂等更新）。"""
    for record in records:
        db.documents.update_one(
            {"source": record["source"], "doc_id": record["doc_id"]},
            {"$set": record},
            upsert=True,
        )


def write_mining_results(db: Any, results: list[dict[str, Any]]) -> None:
    """写入 mining_results 集合，并回填对应 documents.mining 摘要引用。"""
    for result in results:
        db.mining_results.insert_one(dict(result))
        db.documents.update_one(
            {"source": result["source"], "doc_id": result["doc_id"]},
            {"$set": {"mining": {
                "batch_id": result["batch_id"],
                "topic_cluster_id": result["topic_cluster_id"],
                "keyword_count": len(result["keywords"]),
            }}},
        )


def write_batch_stats(db: Any, stats: dict[str, Any]) -> None:
    """写入本次运行的 Batch 统计记录（Contract 3：运行统计查询契约）。"""
    db.batches.update_one(
        {"batch_id": stats["batch_id"]},
        {"$set": stats},
        upsert=True,
    )
