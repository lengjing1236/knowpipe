# 个性化知识分类与 Web 展示 —— 端到端验收记录

**Feature**: [spec.md](../../specs/002-personalized-web/spec.md) |
[tasks.md](../../specs/002-personalized-web/tasks.md) |
[quickstart.md](../../specs/002-personalized-web/quickstart.md)

[已验证] 2026-09-16 复核：本记录为历史小规模验收，Step 5 未满足万条数据与人工评价要求，T044 已恢复为未完成。最新改动与测试见 Feature 003–005 evidence。

本文件汇总 quickstart.md 全部 Step 的验收结果，作为课程答辩的证据索引。验收均基于
真实 MongoDB 实例（`knowpipe_mining` 库，Feature 1 已产出的 200 篇文档/210 条
mining_results）与真实 `local[*]` SparkSession，未使用 mongomock/测试替身。

## 测试套件现状

`python3 -m unittest discover -v`：全仓库 88 个测试全部通过，含本 feature 新增的
`tests/web/` 下 mongo_sink、auth、classify、baseline、score_job、routes_api 各模块
测试（其中 score_job 使用真实 `local[*]` Spark 会话，非 mock）。未对 Feature 1 现有
测试造成回归。

## Step 1：最小垂直切片——注册登录 + 画像录入 + 判定 + 页面展示（User Story 1，对应 SC-001）— 已完成

- 通过真实 Flask 开发服务器（`python3 -m knowpipe.web.app`，连接真实 MongoDB）
  完成注册、登录、`GET /api/topics`、`POST /api/profile/topics`（设置已知主题）、
  `GET /api/recommendations?mode=personalized` 全链路调用。
- `GET /api/topics` 返回的主题簇按 `document_count` 降序排列。
- 设置已知主题后请求推荐结果，验证每条 `items[]` 均带有非空 `status` 与可读判定
  依据（`matched_keywords`/`matched_topic_cluster_id`）；例如关键词 `angular` 命中
  已知主题簇 0 后，对应知识单元判定为 `known`。

## Step 2：新用户默认状态（对应 spec Edge Cases）— 已完成

- 新注册且未设置任何已知主题/已读文档的用户，请求
  `GET /api/recommendations?mode=personalized` 返回全部 37 条知识单元均为
  `status: "new"`，符合"无画像基础时不应臆断已知"的预期。

## Step 3：鉴权边界（对应 spec FR-004/FR-005、contracts/api-contract.md 鉴权模型）— 已完成

- 未登录请求 `GET /api/recommendations` → 返回 401。
- 已登录用户携带另一用户的 `user_id` 发起请求（跨用户越权）→ 返回 403。

## Step 4：反馈闭环（User Story 3，对应 spec SC-004、FR-009/017/019）— 已完成

- 选取一条 `status: "new"` 的知识单元 `kw:allocator`，提交
  `POST /api/profile/feedback`（`action=confirmed_known`）。
- 重新请求推荐结果，确认该知识单元判定状态由 `new` 变为 `known`，验证反馈触发的
  画像更新与重新判定（`classify.classify_all` + `mongo_sink.upsert_user_knowledge_status`）
  按预期生效。

## Step 5：规模化判定 + 个性化/基线对比（User Story 2，对应 spec SC-002/SC-003）— 小规模链路已验证；万条规模与人工质量评价待完成

- 运行 `python3 -m knowpipe.web.score_job --mongo-uri mongodb://localhost:27017
  --mongo-db knowpipe_mining`：
  - `batch_id`: `20260905-97b399c7`
  - `input_count=96`，`valid_count=96`，`failed_count=0`，`status=success`
  - 产出约 74 条 `user_knowledge` 评分记录，覆盖 2 个注册用户 × 37 个知识单元
  - 按 `batch_id` 查询运行统计在秒级完成
- 对比 `mode=personalized` 与 `mode=baseline` 两组 `GET /api/recommendations`
  返回：`mode=baseline` 的条目不含 `status`/`matched_keywords`/
  `matched_topic_cluster_id` 字段（对应 Contract 6 baseline 分支约定），两组
  结果集顺序/内容不同，只能验证两种排序路径有区别，不能证明推荐质量提升。

**规模说明**：本次运行基于 Feature 1 当前已产出的真实数据规模（200 篇文档/
210 条挖掘结果），验证了 `score_job.py` 的计算逻辑、批次可追溯性写入
（复用 `knowpipe/mining/batch.py`）与个性化/基线对比链路均按预期工作。Feature 1
的 ≥10,000 条规模化运行仍处于 StackExchange IP 限流封锁中（见
[Feature 1 acceptance-record.md](../001-data-pipeline-spark-mining/acceptance-record.md)
Step 4 说明），待该封锁解除并产出最终规模化批次后，本 Step 5 可直接对同一批
`documents`/`mining_results` 重跑 `score_job.py` 得到万级规模的最终证据，
`knowpipe/web/` 侧代码本身不依赖该规模限制、无需改动。

## 存储边界与错误处理审计（T050/T051，对应 Feature 1 storage-contract.md 与 T043 模式）— 已完成

- `grep` 确认 `knowpipe/web/*.py` 仅通过 `from ..mining import batch as batch_mod`
  复用批次证据结构，未导入 `knowpipe.mining` 其他内部实现；未出现任何
  `db.documents.(insert|update|delete)` 或
  `db.mining_results.(insert|update|delete)` 调用，满足只读消费边界。
- `knowpipe/web/auth.py`/`routes_api.py` 补充了 `logging` 记录：未登录拒绝、
  session 失效拒绝、越权拒绝、用户名重复注册、登录凭据错误五处关键拒绝路径均有
  可追溯日志；`score_job.py` 的失败路径已复用 Feature 1 T043 模式，在异常时也
  落盘 `status="failed"` 的批次统计。
