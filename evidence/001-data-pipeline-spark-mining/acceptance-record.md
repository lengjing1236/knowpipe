# 数据管道与 Spark 挖掘 —— 端到端验收记录

**Feature**: [spec.md](../../specs/001-data-pipeline-spark-mining/spec.md) |
[tasks.md](../../specs/001-data-pipeline-spark-mining/tasks.md) |
[quickstart.md](../../specs/001-data-pipeline-spark-mining/quickstart.md)

本文件汇总 Phase 0 环境验证与 quickstart.md 全部 Step 的验收结果，作为课程答辩的
证据索引。逐项标注状态：已完成/待补充，避免用未最终确认的临时数据冒充最终证据。

## Phase 0：环境验证（research.md §1）

| 项目 | 结果 |
|---|---|
| PySpark 可导入 | 已验证，`pyspark==4.2.0`（含 py4j 0.10.9.9） |
| `local[*]` SparkSession 可用 | 已验证，Java 17.0.20 运行时 |
| PyMongo 连接真实 MongoDB 实例 | 已验证，本机部署 MongoDB Community 7.0.14
（`~/mongodb-local`，非 mongomock），`admin.command('ping')` 返回 `{'ok': 1.0}` |

无遗留阻塞项，详见 research.md §1。

## Step 1/2：最小链路 + 批次可追溯性（User Story 1，对应 SC-001/SC-003）— 已完成

- **batch_id**: `20260905-7442679d`
- `input_count=200`，`skipped_count=0`，`failed_count=0`
- `documents` 集合记录数 = 200，`mining_results` 集合记录数 = 200
- 抽样记录的 `keywords`/`topic_cluster_id`/`similar_doc_ids` 均非空
- 按 `batch_id` 查询运行统计耗时 0.007s（要求 1 分钟内，远超达标）

详见 tasks.md T034。

## Step 3：幂等更新验收（User Story 3，对应 FR-012/SC-004）— 已完成

- 验收方式：`tests/mining/test_mongo_sink.py` 中
  `test_repeat_upsert_with_unchanged_content_does_not_duplicate`、
  `test_repeat_upsert_with_changed_content_updates_not_duplicates`、
  `test_mining_results_accumulates_history_across_batches_without_deleting_old`
  三个用例，用 mongomock 精确复现 quickstart Step 3 描述的两种场景（内容不变
  重复处理、内容变更后重新处理），全部通过。
- 结论：`documents` 集合按 `(source, doc_id)` upsert，内容不变时记录数不增长、
  内容变更时原地更新为最新内容；`mining_results` 按批次追加历史记录（同一
  `doc_id` 可对应多条不同 `batch_id` 的历史结果），`documents.mining` 摘要引用
  始终指向最新批次——与 quickstart.md Step 3 的预期结果完全一致。
- 详见 tasks.md T039/T040/T041。

## Step 4：规模化运行验收（User Story 2，对应 SC-002/SC-005）— 待补充

**当前状态：尚未产出最终课程证据批次。**

已用修复后的代码（性能修复 + 数据完整性修复，见下）完整跑通一次
9,586(stackexchange)+400(arxiv)=9,986 条有效文档的真实规模化运行
（临时 batch_id `20260905-7bc45fb6`，全程无需人工中途干预，
`input_count=10000, valid_count=9997, skipped_count=3, failed_count=0`），
验证了性能修复和数据完整性修复均生效，运行日志存档于
[`run_9986_prefinal_perf_fix_validation.log`](run_9986_prefinal_perf_fix_validation.log)。

但该批次因两个原因不能作为最终课程证据：
1. StackExchange 当日配额仅剩 96 次请求，实际未达到 10,000 条门槛（差 4 次）；
2. 跑完后发现了跨站点 `doc_id` 冲突的数据完整性缺陷（11 条静默覆盖），已在事后
   修复代码并清空了该批次在 `knowpipe` 数据库中的数据（`documents`/`mining_results`/
   `batches` 三个集合按 `batch_id`/`source` 精确过滤删除，`knowpipe_mining` 数据库中
   Step 1/2/3 用到的历史验收数据未受影响）。

随后该 IP 被 StackExchange 判定触发 `error_id 502 throttle_violation`，
封锁全部请求约 22 小时（预计 2026-09-06 21:25 左右解除）。**待封锁解除后，需用
当前已修复的代码重新完整跑一次 ≥10,000 条规模验证，并将本节替换为该次运行的最终
`batch_id`、真实计数与最终运行日志路径**，对应 tasks.md T036/T037/T038。

### 本次运行验证到的两个修复（供最终重跑时复核）

- **性能修复**：`run_mining()` 原实现里同簇相似度两两比较是 O(簇大小²) 的纯
  Python driver 端循环，KMeans 不保证簇大小均衡（`--num-clusters` 只是请求值，
  实测调大到 64 时数据仍只聚成 5 个大簇），导致规模变大后耗时呈平方增长直至卡死
  （10500 条时进程被杀死，退出码 143，无任何输出）。修复为
  `_split_into_bounded_groups()`：超过 `MAX_CLUSTER_COMPARE_SIZE=300` 的簇先用一次
  额外的小规模 Spark KMeans 递归拆分成有界子组再两两比较,只影响相似度比较范围，
  不改变落库的 `topic_cluster_id`。修复后 3000 条合成数据耗时 103s、10500 条耗时
  240s，均能正常跑完。
- **数据完整性修复**：StackExchange `question_id` 只在单站点内唯一，但
  `documents` 集合的唯一键 `(source, doc_id)` 中 `source` 对四个站点都是常量
  `"stackexchange"`，导致跨站点撞号时静默覆盖（真实运行中观察到 11 条）。修复为
  `doc_id` 加站点前缀 `f"{site}-{question_id}"`，保证全局唯一。

## 运行时错误处理与运行记录完整性（T043，对应 FR-010/FR-011）

审计确认并修复了两个缺口：
- `spark_job.main()` 此前在写入 `batches` 集合之前的任何异常（Spark 阶段或
  Mongo 写入阶段）都会导致运行统计永久丢失。已改为异常路径也落盘
  `status="failed"` 与 `error_message`，成功路径落盘 `status="success"`，
  并有回归测试
  `tests/mining/test_spark_job.py::TestMainWritesBatchStatsOnFailure` 覆盖。
- 校验失败的具体原因（`DocumentValidationError` 消息）、采集重试的每次失败
  原因此前完全静默（仅计数或仅在最终耗尽重试后可见）。已改为通过标准 `logging`
  模块记录，`__main__` 入口配置了 `logging.basicConfig`。

## 测试套件现状

`python3 -m unittest discover -v`：35 个测试全部通过（含本 feature 新增的
mongo_sink 幂等性测试、stackexchange 多站点轮换测试、spark_job 失败路径测试）。
