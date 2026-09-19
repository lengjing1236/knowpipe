"""推荐排序分数计算：PySpark 批量作业（research.md §2 公式，不含来源多样性 δ 项）。

分数只影响排序，不影响 classify.py 产出的 status（data-model.md State Transitions）。
复用 knowpipe/mining/spark_job.py 的 local[*] 会话模式与 knowpipe/mining/batch.py 的
批次证据结构（new_batch_id/BatchStats），只读查询 documents/mining_results。
"""
from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from ..mining import batch as batch_mod
from . import mongo_sink

logger = logging.getLogger(__name__)

ALPHA = 0.4  # 主题相关度权重
BETA = 0.4  # 新增关键词比例权重
GAMMA = 0.2  # 文档质量权重
EPSILON = 0.5  # 已读/重复惩罚权重


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="推荐排序分数计算作业（Spark）")
    parser.add_argument("--mongo-uri", required=True, help="MongoDB 连接串")
    parser.add_argument("--mongo-db", default=None,
                         help="数据库名（缺省使用连接串中的默认数据库）")
    return parser


def _collect_rows(db: Any) -> list[dict[str, Any]]:
    """组装 (user_id, knowledge_id) 维度的打分输入行：每个知识单元的关键词/主题簇/
    可靠性，叠加每个已注册用户的已知关键词/已读文档信号。"""
    units = mongo_sink.get_knowledge_units(db)
    users = list(db.users.find({}))
    rows: list[dict[str, Any]] = []
    for user in users:
        user_id = user["user_id"]
        profile = mongo_sink.get_or_create_profile(db, user_id)
        known_keywords = set(profile.get("known_keywords", []))
        known_topics = {str(t) for t in profile.get("known_topics", [])}
        read_keys = {(r.get("source"), r.get("doc_id")) for r in profile.get("read_doc_ids", [])}

        for unit in units:
            doc_keywords = set(unit.get("keywords") or [])
            topic_id = unit.get("topic_cluster_id")
            source, doc_id = unit.get("source"), unit.get("doc_id")

            topic_relevance = 1.0 if topic_id is not None and str(topic_id) in known_topics else 0.0
            new_keyword_ratio = (len(doc_keywords - known_keywords) / len(doc_keywords)
                                  if doc_keywords else 0.0)

            reliable = True
            if source and doc_id:
                doc = db.documents.find_one({"source": source, "doc_id": doc_id})
                latest_batch = ((doc or {}).get("mining") or {}).get("batch_id")
                mr = db.mining_results.find_one({"source": source, "doc_id": doc_id, "batch_id": latest_batch})
                if mr is not None:
                    reliable = bool(mr.get("reliable", True))
            doc_quality = 1.0 if reliable else 0.3
            read_penalty = 1.0 if (source, doc_id) in read_keys else 0.0

            rows.append({
                "user_id": user_id,
                "knowledge_id": unit["knowledge_id"],
                "topic_relevance": topic_relevance,
                "new_keyword_ratio": new_keyword_ratio,
                "doc_quality": doc_quality,
                "read_penalty": read_penalty,
            })
    return rows


def compute_scores(spark, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用 PySpark DataFrame 操作按公式批量计算 score（不含来源多样性 δ 项）。"""
    if not rows:
        return []
    from pyspark.sql.functions import col, lit

    df = spark.createDataFrame(rows)
    scored_df = df.withColumn(
        "score",
        lit(ALPHA) * col("topic_relevance")
        + lit(BETA) * col("new_keyword_ratio")
        + lit(GAMMA) * col("doc_quality")
        - lit(EPSILON) * col("read_penalty"),
    )
    return [
        {"user_id": row["user_id"], "knowledge_id": row["knowledge_id"], "score": float(row["score"])}
        for row in scored_df.select("user_id", "knowledge_id", "score").collect()
    ]


def run(db: Any, spark) -> tuple[str, dict[str, Any]]:
    """跑一次完整的推荐分数批次：采集输入 → Spark 计算 → 写入 user_knowledge → 记录批次统计。

    对应 FR-010 的运行记录留存要求：即便计算或写入阶段失败，也把已知统计和失败
    原因落盘到 batches 集合，与 Feature 1 spark_job.py 的失败处理模式一致。
    """
    batch_id = batch_mod.new_batch_id()
    stats = batch_mod.BatchStats(batch_id, sources=["user_knowledge_score"])

    rows = _collect_rows(db)
    stats.input_count = len(rows)
    stats.spark_application_id = spark.sparkContext.applicationId
    stats.spark_master = spark.sparkContext.master
    try:
        scored = compute_scores(spark, rows)
        mongo_sink.write_score_batch(db, scored, batch_id)
        stats.valid_count = len(scored)
    except Exception as e:  # noqa: BLE001
        logger.exception("推荐分数批次 %s 处理失败", batch_id)
        stats.finish(status="failed", error_message=str(e))
        db.batches.update_one({"batch_id": batch_id}, {"$set": stats.to_dict()}, upsert=True)
        raise

    stats.finish(status="success")
    db.batches.update_one({"batch_id": batch_id}, {"$set": stats.to_dict()}, upsert=True)
    return batch_id, stats.to_dict()


def main(argv: list[str] | None = None) -> str:
    from pymongo import MongoClient
    from pyspark.sql import SparkSession

    args = build_arg_parser().parse_args(argv)
    client = MongoClient(args.mongo_uri)
    db = client[args.mongo_db] if args.mongo_db else client.get_default_database()
    mongo_sink.ensure_indexes(db)

    spark = SparkSession.builder.master(os.environ.get("SPARK_MASTER", "local[2]")).appName("knowpipe-web-score").getOrCreate()
    try:
        batch_id, _ = run(db, spark)
    finally:
        spark.stop()

    return batch_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(main())
