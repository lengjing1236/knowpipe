"""Spark 挖掘作业入口：清洗/去重（Python 预过滤）→ TF-IDF → KMeans → 相似度 → 写入 MongoDB。

预过滤（采集/校验/去重）在纯 Python 中完成，只有清洗后的有效文档才进入 Spark
DataFrame 做 TF-IDF/KMeans；这是 research.md §3 的直接结论：Spark 的批量算子不支持
逐行异常恢复，所以"跳过无效文档"必须发生在进入这些算子之前。
"""
from __future__ import annotations

import argparse
import logging
import os
from functools import partial
import re
from typing import Any

from . import batch as batch_mod
from . import contract as contract_mod
from . import dedup as dedup_mod
from .collectors import arxiv as arxiv_collector
from .collectors import stackexchange as stackexchange_collector

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 10
DEFAULT_NUM_CLUSTERS = 8
DEFAULT_TOP_SIMILAR = 5
# KMeans 不保证各簇大小均衡，单簇内两两相似度比较是 O(size^2) 的Spark task 内有界循环，
# 簇过大时会在万级规模下卡死。超过该阈值的簇在收集前先用一次额外的 Spark KMeans 拆分成
# 更小的子组，保证任意一次两两比较的规模有上限（子组只影响相似度比较范围，不改变落库的
# topic_cluster_id）。
MAX_CLUSTER_COMPARE_SIZE = 300
MAX_SPLIT_DEPTH = 4

COLLECTORS = {
    "stackexchange": stackexchange_collector.collect,
    "arxiv": arxiv_collector.collect,
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="数据管道与 Spark 挖掘作业")
    parser.add_argument("--sources", nargs="+", required=True,
                         help="数据源列表，如 stackexchange arxiv")
    parser.add_argument("--target-count", type=int, required=True,
                         help="默认目标文档数，未在 --source-target-count 中单独指定的来源使用该值")
    parser.add_argument("--source-target-count", nargs="+", default=[],
                         metavar="SOURCE=COUNT",
                         help="按来源覆盖目标数，如 stackexchange=10000 arxiv=500"
                              "（arXiv 为补充源，不计入 10,000 条规模门槛，见调研文档 2.2 节）")
    parser.add_argument("--mongo-uri", required=True, help="MongoDB 连接串")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                         help="每条文档保留的关键词数量上限")
    parser.add_argument("--num-clusters", type=int, default=DEFAULT_NUM_CLUSTERS,
                         help="KMeans 主题簇数量")
    return parser


def prepare_valid_records(sources: list[str], target_count: int,
                           stats: batch_mod.BatchStats,
                           source_target_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """采集 + 字段校验 + 两级去重（跨来源，本批次内）。返回可进入 Spark 处理的有效文档。"""
    valid_records: list[dict[str, Any]] = []
    seen_exact_keys: set[tuple[str, str]] = set()
    seen_hashes: set[str] = set()
    source_target_counts = source_target_counts or {}

    for source in sources:
        collect_fn = COLLECTORS[source]
        this_target = source_target_counts.get(source, target_count)
        try:
            for raw in collect_fn(this_target):
                stats.input_count += 1
                try:
                    record = contract_mod.validate_document(raw)
                except contract_mod.DocumentValidationError as e:
                    logger.warning("文档校验失败，跳过（来源=%s）：%s", source, e)
                    stats.skipped_count += 1
                    continue

                key = dedup_mod.exact_key(record)
                if key in seen_exact_keys:
                    stats.skipped_count += 1
                    continue

                content_h = dedup_mod.content_hash(record)
                if content_h in seen_hashes:
                    stats.skipped_count += 1
                    continue

                seen_exact_keys.add(key)
                seen_hashes.add(content_h)
                record["quality"]["dedup_hash"] = content_h
                valid_records.append(record)
        except Exception as e:  # noqa: BLE001
            # 单个来源采集失败（如补充源 arXiv 触发限流）不应丢弃其他来源已采集到的有效
            # 文档——尤其是主源 StackExchange 往往已消耗大量每日请求配额，重新整批失败
            # 的代价远高于跳过一个来源继续跑完剩余来源。
            logger.warning("来源 %s 采集中断，跳过该来源剩余部分，已保留其它来源结果: %s", source, e)

    return valid_records


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z]{2,}", (text or "").lower())


def _cosine_similarity(vec_a, vec_b) -> float:
    norm_a = float(vec_a.norm(2))
    norm_b = float(vec_b.norm(2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(vec_a.dot(vec_b)) / (norm_a * norm_b)


def _split_into_bounded_groups(spark, members: list[tuple[str, Any]], max_size: int,
                                depth: int = 0) -> list[list[tuple[str, Any]]]:
    """把一个（可能过大的）KMeans 簇拆成若干不超过 max_size 的子组，用于限定两两相似度
    比较的规模——KMeans 不保证簇大小均衡，单簇内两两比较是 O(size^2) 的 driver 端循环，
    簇过大时会在万级规模下卡死。子组只影响相似度比较范围，不改变落库的 topic_cluster_id。
    对子组再跑一次 KMeans（比全量 fit 快得多，因为规模已小很多）；超过 MAX_SPLIT_DEPTH 仍
    未收敛时改用确定性等分，保证一定能拆到阈值以下。
    """
    if len(members) <= max_size:
        return [members]
    if depth >= MAX_SPLIT_DEPTH:
        return [members[i:i + max_size] for i in range(0, len(members), max_size)]

    from pyspark.ml.clustering import KMeans

    sub_k = min(max(2, -(-len(members) // max_size)), len(members))
    sub_df = spark.createDataFrame(list(members), ["doc_id", "features"])
    kmeans = KMeans(k=sub_k, seed=42, featuresCol="features", predictionCol="sub_cluster_id")
    sub_clustered = kmeans.fit(sub_df).transform(sub_df).select("doc_id", "sub_cluster_id").collect()

    vec_by_id = dict(members)
    sub_groups: dict[int, list[tuple[str, Any]]] = {}
    for row in sub_clustered:
        sub_groups.setdefault(int(row["sub_cluster_id"]), []).append((row["doc_id"], vec_by_id[row["doc_id"]]))

    bounded: list[list[tuple[str, Any]]] = []
    for group in sub_groups.values():
        bounded.extend(_split_into_bounded_groups(spark, group, max_size, depth + 1))
    return bounded


def rank_similarity_group(members, top_similar=DEFAULT_TOP_SIMILAR):
    """Rank one bounded candidate group on a Spark executor, never on the driver."""
    from pyspark import TaskContext
    if TaskContext.get() is None:
        raise RuntimeError('similarity ranking must run in a Spark task')
    for doc_id, vector in members:
        scored = [(other_id, _cosine_similarity(vector, other_vector))
                  for other_id, other_vector in members if other_id != doc_id]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: (-item[1], item[0]))
        yield doc_id, [other_id for other_id, _ in scored[:top_similar]]


def run_mining(spark, valid_records: list[dict[str, Any]], top_k: int = DEFAULT_TOP_K,
               num_clusters: int = DEFAULT_NUM_CLUSTERS, top_similar: int = DEFAULT_TOP_SIMILAR,
               stats: batch_mod.BatchStats | None = None) -> dict[str, dict[str, Any]]:
    """对有效文档批量执行 TF-IDF + KMeans + 相似度计算，返回 doc_id -> MiningResult 字段。

    分词结果为空的文档（如正文全是非字母字符）在进入 Spark DataFrame 之前被过滤并计入
    failed_count——这就是"单条文档处理异常隔离"在 Spark DataFrame 模型下的实现方式
    （对应 FR-011、research.md §3），而不是在 DataFrame 算子内部包裹 try/except。
    """
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import CountVectorizer, IDF, RegexTokenizer, StopWordsRemover

    if not valid_records:
        return {}

    usable_records = []
    for record in valid_records:
        if not _tokenize(record.get("body_text", "")):
            if stats is not None:
                stats.failed_count += 1
            continue
        usable_records.append(record)

    if not usable_records:
        return {}

    rows = [(r["doc_id"], r["body_text"]) for r in usable_records]
    df = spark.createDataFrame(rows, ["doc_id", "body_text"])

    tokenizer = RegexTokenizer(inputCol="body_text", outputCol="raw_tokens", pattern=r"[^a-zA-Z]+")
    remover = StopWordsRemover(inputCol="raw_tokens", outputCol="tokens")
    cv = CountVectorizer(inputCol="tokens", outputCol="tf", vocabSize=20000, minDF=1.0)
    idf = IDF(inputCol="tf", outputCol="features")

    tokenized_df = remover.transform(tokenizer.transform(df))
    cv_model = cv.fit(tokenized_df)
    tf_df = cv_model.transform(tokenized_df)
    idf_model = idf.fit(tf_df)
    tfidf_df = idf_model.transform(tf_df)

    k = max(1, min(num_clusters, tfidf_df.count()))
    kmeans = KMeans(k=k, seed=42, featuresCol="features", predictionCol="topic_cluster_id")
    clustered_df = kmeans.fit(tfidf_df).transform(tfidf_df)

    vocab = cv_model.vocabulary
    collected = clustered_df.select("doc_id", "features", "topic_cluster_id").collect()

    results: dict[str, dict[str, Any]] = {}
    vectors_by_cluster: dict[int, list[tuple[str, Any]]] = {}
    for row in collected:
        vec = row["features"]
        pairs = sorted(zip(vec.indices, vec.values), key=lambda p: -p[1])[:top_k]
        keywords = [{"term": vocab[i], "weight": float(w)} for i, w in pairs]
        cluster_id = int(row["topic_cluster_id"])
        results[row["doc_id"]] = {
            "doc_id": row["doc_id"],
            "keywords": keywords,
            "topic_cluster_id": cluster_id,
            "similar_doc_ids": [],
        }
        vectors_by_cluster.setdefault(cluster_id, []).append((row["doc_id"], vec))

    # Candidate grouping remains bounded; pairwise cosine ranking runs in Spark tasks.
    bounded_groups = []
    for members in vectors_by_cluster.values():
        bounded_groups.extend(_split_into_bounded_groups(spark, members, MAX_CLUSTER_COMPARE_SIZE))
    partitions = max(1, min(len(bounded_groups), spark.sparkContext.defaultParallelism))
    rankings = (spark.sparkContext.parallelize(bounded_groups, partitions)
                .flatMap(partial(rank_similarity_group, top_similar=top_similar)).collect())
    for doc_id, neighbors in rankings:
        results[doc_id]['similar_doc_ids'] = neighbors

    return results


def main(argv: list[str] | None = None) -> str:
    """运行一次挖掘批次：采集→校验→去重→Spark 挖掘→写入 MongoDB，返回 batch_id。"""
    from pyspark.sql import SparkSession
    from pymongo import MongoClient

    from . import mongo_sink

    args = build_arg_parser().parse_args(argv)

    source_target_counts = {}
    for pair in args.source_target_count:
        name, _, count = pair.partition("=")
        source_target_counts[name] = int(count)

    batch_id = batch_mod.new_batch_id()
    stats = batch_mod.BatchStats(batch_id, args.sources)

    valid_records = prepare_valid_records(args.sources, args.target_count, stats,
                                           source_target_counts)

    client = MongoClient(args.mongo_uri)
    db = client.get_default_database()
    mongo_sink.ensure_indexes(db)

    try:
        spark = SparkSession.builder.master(os.environ.get("SPARK_MASTER", "local[2]")).appName("knowpipe-mining").getOrCreate()
        try:
            mining_map = run_mining(spark, valid_records, top_k=args.top_k,
                                     num_clusters=args.num_clusters, stats=stats)
            stats.spark_application_id = spark.sparkContext.applicationId
            stats.spark_master = spark.sparkContext.master
        finally:
            spark.stop()

        mongo_sink.upsert_documents(db, valid_records)

        mining_results = []
        for record in valid_records:
            mined = mining_map.get(record["doc_id"])
            if mined is None:
                continue
            mining_results.append({
                "doc_id": record["doc_id"],
                "source": record["source"],
                "batch_id": batch_id,
                "keywords": mined["keywords"],
                "topic_cluster_id": mined["topic_cluster_id"],
                "similar_doc_ids": mined["similar_doc_ids"],
                "reliable": not record["quality"].get("short_body", False),
            })
            stats.valid_count += 1
        mongo_sink.write_mining_results(db, mining_results)
    except Exception as e:
        # FR-010 要求每次运行都留存可核查的运行记录，即便本次运行在 Spark 处理或
        # MongoDB 写入阶段失败，也必须把目前已知的统计信息（起止时间、已采集/已跳过
        # 计数）和失败原因落盘到 batches 集合，而不是让异常直接吞掉这些已积累的信息。
        logger.exception("批次 %s 处理失败", batch_id)
        stats.finish(status="failed", error_message=str(e))
        mongo_sink.write_batch_stats(db, stats.to_dict())
        raise

    stats.finish(status="success")
    mongo_sink.write_batch_stats(db, stats.to_dict())

    return batch_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(main())
