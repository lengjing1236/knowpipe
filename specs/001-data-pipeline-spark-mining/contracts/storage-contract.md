# Interface Contract: Storage Contract（documents / mining_results）

**Feature**: [spec.md](spec.md)

本 feature 不对外暴露 HTTP API（对外 API 属于下游"Web API" feature）。本 feature
真正的"接口"是它写入 MongoDB 的两个集合的数据契约——下游"个性化知识分类"和
"Web API" feature 都直接读取这两个集合，因此把它们的字段保证作为本 feature 对
外的契约固定下来。

## Contract 1: `documents` 集合读取契约

**保证给下游的内容**：
- 每条记录满足 `data-model.md` 中 Document 实体的全部必填字段非空。
- `source + doc_id` 全局唯一：下游按此组合查询，永远最多命中一条记录。
- `batch_id` 字段可用于反查 Batch 运行记录（见 Contract 3）。
- `quality.short_body = true` 的记录，其关联的 `mining_results.reliable` 必为
  `false`——下游可以只凭 `documents.quality.short_body` 判断是否需要谨慎对待该
  文档的挖掘结果，不必额外查询 `mining_results`。

**不保证的内容**（明确排除，避免下游误用）：
- 不保证 `mining` 摘要字段在文档刚写入时就已回填（Spark 挖掘阶段可能滞后于
  documents 的写入）；下游若需要挖掘结果，应查询 `mining_results` 而非仅依赖
  `documents.mining`。
- 不保证同一 `doc_id` 只有一条历史记录——upsert 更新的是"当前内容"，但历史批次
  的 `mining_results` 记录不会被删除（见 data-model.md 的关联约束）。

## Contract 2: `mining_results` 集合读取契约

**保证给下游的内容**：
- 每条记录满足 Document 校验通过后才会产出（无效/失败文档不产出记录）。
- `keywords` 非空数组、`topic_cluster_id` 非空——下游做个性化判定时可以假设这
  两个字段总是存在，不需要做空值防御（除非 `reliable = false`，此时下游应自行
  决定是否降权使用）。
- 可通过 `doc_id + batch_id` 精确查询到某一次运行产出的具体结果。

**不保证的内容**：
- 不保证"最新批次"的判定方式（多个批次可能都对同一 `doc_id` 有记录）；下游若
  需要"当前有效结果"语义，需要自行按时间戳或显式指针选取，本 feature 不提供
  这层业务语义。

## Contract 3: 运行统计（Batch）查询契约

**保证给下游/课程证据审查者的内容**：
- 给定任意 `batch_id`，可在 1 分钟内查到该批次的起止时间、输入条数、有效产出数、
  跳过数、失败数（对应 spec SC-003）。
- 具体存储位置（独立集合或运行日志文件）由实施阶段决定，但查询入口（函数或
  查询方式）必须在 quickstart.md 中给出可执行示例。

## Invocation Contract: Spark 作业触发方式

本 feature 对"如何触发一次处理运行"的契约（供人工或未来的调度脚本调用）：

- 输入：来源类型集合（`stackexchange`/`arxiv` 或两者）、目标条数或全部可用数据、
  MongoDB 连接信息。
- 输出：一个 `batch_id`（成功触发后立即返回，不等待整个批次跑完也应可查询到
  该 `batch_id` 已存在但未完成的运行记录）。
- 幂等性：对同一来源的同一批原始数据重复触发，不会在 `documents` 中产生重复
  记录（FR-012），但会产生新的 `batch_id` 和对应的 `mining_results` 历史记录。
- 具体命令行/函数签名形式（如 `spark-submit` 参数）由实施阶段的 tasks.md 决定，
  本契约只固定输入输出语义，不固定调用形式。
