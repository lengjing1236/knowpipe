# Data Model: 个性化知识分类与 Web 展示

**Feature**: [spec.md](spec.md) | 新增集合字段结构参考《课程项目选题调研与实施
方案.md》第 5.4 节，本文档补充校验规则与状态转换；只读依赖的 `documents`/
`mining_results` 字段定义见 Feature 1 的
[data-model.md](../001-data-pipeline-spark-mining/data-model.md)，不重复列出。

## Entity: User（对应 MongoDB `users` 集合，新增）

| 字段 | 类型 | 说明 | 校验规则 |
|---|---|---|---|
| `user_id` | string | 用户唯一标识（注册时生成或使用用户名） | 非空，全局唯一 |
| `username` | string | 登录用户名 | 非空，全局唯一 |
| `password_hash` | string | Werkzeug `generate_password_hash` 产出的哈希 | 非空；原始密码不落库、不写日志 |
| `created_at` | datetime | 注册时间 | 非空 |

**唯一性约束**：`user_id` 唯一索引；`username` 唯一索引（登录查找入口）。

**安全说明**：`password_hash` 不得以任何形式出现在 API 响应体中；查询用户信息
的 API 返回值必须显式排除该字段。

## Entity: UserProfile（对应 MongoDB `user_profiles` 集合，新增）

| 字段 | 类型 | 说明 | 校验规则 |
|---|---|---|---|
| `user_id` | string | 关联 User.user_id | 非空，必须能在 `users` 中找到对应记录 |
| `known_topics` | array[string] | 用户显式设置的已知主题簇标识（`topic_cluster_id` 的字符串形式） | 允许为空数组（新用户默认无已知主题） |
| `known_keywords` | array[string] | 从已知主题和已读文档中派生的已知关键词集合（用于关键词级匹配） | 允许为空数组；由系统在已知主题/已读文档变更时重新派生，不直接由用户编辑 |
| `read_doc_ids` | array[{source: string, doc_id: string}] | 用户已读过的文档标识列表 | 允许为空数组 |
| `feedback_history` | array[{knowledge_id: string, action: string, created_at: datetime}] | 反馈记录，`action` 取值 `confirmed_known` \| `useful` \| `irrelevant` | 允许为空数组；同一 `knowledge_id` 可有多条历史记录，以 `created_at` 最新的一条为准（spec FR-019） |
| `updated_at` | datetime | 画像最近一次变更时间 | 非空 |

**唯一性约束**：`user_id` 唯一索引（一个用户一份画像）。

**派生规则**：`known_keywords` 不是用户直接输入的字段，而是系统在
`known_topics` 或 `read_doc_ids` 变化时，从对应主题簇/已读文档的挖掘结果中
聚合出的高权重关键词集合重新计算得到；这样"新增已知主题"和"标记已读"两种
操作都能一致地反映到关键词级匹配上。

## Entity: UserKnowledge（对应 MongoDB `user_knowledge` 集合，新增）

| 字段 | 类型 | 说明 | 校验规则 |
|---|---|---|---|
| `user_id` | string | 关联 User.user_id | 非空 |
| `knowledge_id` | string | 知识单元标识：关键词知识单元格式 `kw:{term}`，主题簇知识单元格式 `topic:{topic_cluster_id}` | 非空 |
| `source` / `doc_id` | string | 该知识单元所属的代表性文档（关键词/主题簇通常关联多篇文档，取产出该判定时的具体来源文档用于展示） | 非空 |
| `status` | string enum | `known` \| `refine` \| `new` \| `possible_conflict` | 必须是枚举值之一；`possible_conflict` 必须同时存在非空 `conflict_evidence`，否则写入时降级为 `refine`（有主题簇命中）或 `new`（无命中），对应 spec FR-003 |
| `matched_keywords` | array[string] | 命中用户已知关键词集合的具体关键词（判定依据） | 允许为空数组 |
| `matched_topic_cluster_id` | int \| null | 命中用户已知主题簇集合的具体簇 id（判定依据） | 命中时非空，否则为 null |
| `conflict_evidence` | object \| null | `{snippet: string, source_doc_ids: array[string]}`，`possible_conflict` 的可追溯证据 | 仅 `status = possible_conflict` 时非空；`pending_review = true` 期间即使存在候选证据，对外查询仍不返回该状态（见状态转换） |
| `pending_review` | bool | 该记录是否为待人工复核的冲突候选 | 默认 false；由 LLM 生成候选冲突时置 true，人工复核通过后置 false |
| `score` | float | 推荐排序分数（仅 Spark 作业写入，`classify.py` 不修改此字段） | 由 `score_job.py` 产出，默认 0.0 |
| `batch_id` | string | 产出本条推荐分数的 Spark 批次标识（关联 Feature 1 的 Batch 实体或本 feature 自己的批次记录，见下方 Batch 说明） | 非空 |
| `updated_at` | datetime | 本条判定记录最近一次更新时间 | 非空 |

**关联约束**：`user_id + knowledge_id` 复合唯一索引（同一用户对同一知识单元
只保留最新判定，不像 Feature 1 的 `mining_results` 那样保留历史批次记录——
判定结果代表"当前状态"而非"历史留痕"，历史通过 `UserProfile.feedback_history`
体现）。

## Batch（本 feature 复用 Feature 1 的批次证据模式）

推荐排序分数计算（`score_job.py`）产出的 `batch_id`，落地在与 Feature 1 相同的
`batches` 集合中（复用 `knowpipe/mining/batch.py` 的 `BatchStats`/`new_batch_id`，
不重新定义一套批次记录结构），保证课程证据审查时"任意批次标识都能在 1 分钟内
追溯起止时间和统计"这一契约（Feature 1 SC-003）对本 feature 产出的批次同样
成立。

## State Transitions

一个知识单元对某用户的判定状态流转：

```text
用户画像变更（新增已知主题 / 标记已读 / 提交反馈）
  └─ 触发 classify.py 对该用户涉及的知识单元重新判定（纯 Python，不经 Spark）
        ├─ 关键词/主题簇均未命中已知集合 ──────────────→ new
        ├─ 命中已知主题簇，且知识单元含明显新增内容 ───→ refine
        ├─ 关键词高度重合，或命中已知主题簇且无明显新增 → known
        └─ 检测到可能的语义冲突（LLM 辅助生成候选）
              ├─ pending_review = true（人工复核前） ──→ 对外仍展示为 new 或 refine
              └─ 人工复核通过，pending_review = false ─→ 对外展示为 possible_conflict

Feature 1 产出新批次挖掘结果（文档规模扩大或内容更新）
  └─ 触发 score_job.py 重新计算受影响文档的推荐分数
        └─ 更新 UserKnowledge.score / batch_id，不改变 status（status 仍由
           classify.py 按当前画像决定）
```

## Relationship to Feature 1 Entities

- `UserKnowledge.knowledge_id` 为 `topic:{id}` 形式时，`{id}` 对应 Feature 1
  `MiningResult.topic_cluster_id`；为 `kw:{term}` 形式时，`{term}` 取自
  `MiningResult.keywords[].term`。
- `UserKnowledge.source`/`doc_id` 必须能在 Feature 1 的 `documents` 集合中
  查询到对应记录（用于页面展示来源文档标题、链接）。
- 本 feature 不写入、不修改 `documents`/`mining_results` 的任何字段，只读
  查询，遵守 Feature 1 storage-contract.md 中"下游只读消费"的边界。
