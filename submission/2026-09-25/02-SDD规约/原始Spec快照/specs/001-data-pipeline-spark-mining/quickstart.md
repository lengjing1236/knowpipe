# Quickstart: 数据管道与 Spark 挖掘

**Feature**: [spec.md](spec.md) | 字段/契约细节见 [data-model.md](data-model.md)、
[contracts/storage-contract.md](contracts/storage-contract.md)

本指南给出验证本 feature 是否达成 User Story 1（最小链路）与 User Story 2（规模化）
的可执行步骤，不包含完整实现代码。

## 前置条件

1. 环境验证已通过（research.md §1）：
   - `python3 -c "import pyspark; print(pyspark.__version__)"` 可正常输出版本号。
   - `python3 -c "import pymongo; pymongo.MongoClient('<连接串>').admin.command('ping')"`
     可正常连接到 MongoDB 实例（本机容器或课程集群）。
   - 若任一步骤失败，按调研文档第 4 节的备用路径处理（申请 `local[*]` 或课程
     MongoDB 实例），不得用 mongomock/SQLite 替代此处的真实连接验证。
2. 已准备两个来源各 100 条原始文档的样例输入（可来自 Stack Exchange API/arXiv API
   实际拉取，或课程允许的样例数据集）。

## Step 1: 打通最小链路（对应 User Story 1）

```bash
# 具体命令行接口由 tasks.md/实施阶段决定，此处为契约层示例
python3 -m knowpipe.mining.spark_job \
  --sources stackexchange,arxiv --limit-per-source 100 \
  --mongo-uri "<连接串>" --mongo-db knowpipe_mining
```

**预期结果**（对应 spec SC-001）：
- 命令输出一个 `batch_id`。
- 查询 MongoDB：
  ```python
  from pymongo import MongoClient
  db = MongoClient("<连接串>")["knowpipe_mining"]
  assert db.documents.count_documents({}) == 200
  assert db.mining_results.count_documents({"keywords": {"$exists": True, "$ne": []}}) == \
         db.mining_results.count_documents({})
  ```
- 每条 `mining_results` 记录都有非空 `keywords` 和非空 `topic_cluster_id`。

## Step 2: 校验批次可追溯性（对应 SC-003）

```python
batch = db.batches.find_one({"batch_id": batch_id})  # 或对应的运行统计查询方式
assert batch["input_count"] == 200
assert batch["started_at"] and batch["finished_at"]
```

## Step 3: 校验幂等更新（对应 User Story 3 / FR-012）

```bash
# 不改变输入，重新触发一次
python3 -m knowpipe.mining.spark_job \
  --sources stackexchange,arxiv --limit-per-source 100 \
  --mongo-uri "<连接串>" --mongo-db knowpipe_mining
```

**预期结果**：`db.documents.count_documents({})` 仍为 200（无重复记录），但
`db.mining_results` 出现新一批带有新 `batch_id` 的记录（历史结果保留，不覆盖删除）。

## Step 4: 规模化运行（对应 User Story 2）

将 `--limit-per-source` 调整为覆盖不少于 10,000 条有效文档的采集范围，重新触发。

**预期结果**（对应 SC-002）：
- 运行无需人工中途干预即可完成。
- `batch.failed_count` 与 `batch.skipped_count` 均为可读数值（不一定为 0，但必须
  存在且可解释，对应 SC-005）。
- 保留 Spark 运行日志（`spark-submit` 输出或 `SparkContext` 的 UI/日志文件路径）
  作为课程证据，与调研文档第 6 节"基线验收证据"要求对齐。

## 已知限制

- 本 quickstart 不覆盖个性化分类（known/refine/new/possible_conflict）结果的
  展示，那是下游 feature 的验证范围。
- 本 quickstart 不覆盖 HTTP 层面的访问方式，`documents`/`mining_results` 目前
  只能通过直接查询 MongoDB 验证。
