# Data Model: 数据管道与 Spark 挖掘

**Feature**: [spec.md](spec.md) | 字段结构来自《课程项目选题调研与实施方案.md》第
5.2/5.4 节，此处补充校验规则与状态转换，不重复论证选型。

## Entity: Document（对应 MongoDB `documents` 集合）

| 字段 | 类型 | 说明 | 校验规则 |
|---|---|---|---|
| `doc_id` | string | 文档唯一标识（来源内唯一，如 SE 的 question id 或 arXiv 的 arxiv_id） | 非空 |
| `source` | string enum | `stackexchange` \| `arxiv` | 必须是枚举值之一 |
| `source_site` | string | SE 的站点名（如 `stackoverflow`）或 arXiv 分类（如 `cs.DC`） | 非空 |
| `title` | string | 标题 | 非空 |
| `body_text` | string | 清洗后正文/摘要 | 非空，长度 < 阈值时标记 `quality.short_body=true`（Edge Case：过短正文） |
| `body_raw` | string | 原始 HTML/XML | 允许为空（部分来源无法保留原始格式时） |
| `tags` | array[string] | 标签或分类 | 允许为空数组 |
| `language` | string | 语言代码 | 非空，默认 `en` |
| `created_at` | datetime | 创建/发表时间 | 非空 |
| `source_url` | string | 原文链接 | 非空，用于精确去重判定（见 research.md §2） |
| `license` | string | 记录级许可证 | 缺失时填 `unknown`，不得阻塞写入 |
| `quality` | object | 质量标记，如 `{short_body: bool, dedup_hash: string}` | 见下方"质量标记子结构" |
| `batch_id` | string | 产出本记录的处理批次标识 | 非空，关联 Batch 实体 |
| `mining` | object \| null | 本条文档最新一次挖掘结果的摘要引用（详见 MiningResult） | 首次写入时可为 null，挖掘完成后回填 |

**唯一性约束**：`source + doc_id` 唯一索引（对应 spec FR-012 的幂等更新）。

**质量标记子结构**（`quality` 字段内）：
- `short_body: bool` — 正文长度低于阈值（Edge Case 第三条），供下游决定是否采信
  该文档的关键词/主题簇结果。
- `dedup_hash: string` — 清洗后内容的哈希值，用于近似去重判定（research.md §2）。
- `invalid: bool` — 缺失必填字段时置 true 且不写入本集合（FR-002/Edge Case 第一条：
  无效文档被跳过，不产生记录，此字段仅用于运行统计中"跳过原因"的说明，不出现在
  实际写入的文档记录里）。

## Entity: MiningResult（对应 MongoDB `mining_results` 集合）

| 字段 | 类型 | 说明 | 校验规则 |
|---|---|---|---|
| `doc_id` | string | 关联 Document.doc_id | 非空，必须能在 `documents` 中找到对应记录 |
| `source` | string | 关联 Document.source（配合 doc_id 定位） | 非空 |
| `batch_id` | string | 产出本记录的处理批次标识 | 非空，关联 Batch 实体 |
| `keywords` | array[{term: string, weight: float}] | TF-IDF top-k 关键词及权重 | 非空数组（FR-004，至少 1 项，否则该文档挖掘结果视为不可靠，标记见下） |
| `topic_cluster_id` | int | KMeans 主题簇编号 | 非空（FR-005） |
| `similar_doc_ids` | array[string] | 相似度较高的其他文档 doc_id（FR-006） | 允许为空数组（无相近文档时） |
| `reliable` | bool | 本条结果是否可靠（关联 Document.quality.short_body，短正文时置 false） | 默认 true |

**关联约束**：`doc_id + batch_id` 索引，用于按批次追溯（SC-003）。一个 `doc_id`
可以对应多条历史批次的 `MiningResult`（保留处理历史），但下游只应读取最新
`batch_id` 对应的记录——具体"最新"判定方式（时间戳最大或显式当前批次指针）留给
下游 Web API feature 决定，本 feature 只保证数据可追溯，不做"当前有效结果"的
业务判定。

## Entity: Batch（处理批次运行记录）

| 字段 | 类型 | 说明 |
|---|---|---|
| `batch_id` | string | 唯一标识，建议格式 `{YYYYMMDD}-{序号或哈希}` |
| `started_at` / `finished_at` | datetime | 运行起止时间（SC-003） |
| `input_count` | int | 本次输入的原始记录总数 |
| `valid_count` | int | 有效并成功产出记录的文档数 |
| `skipped_count` | int | 因字段缺失/去重被跳过的文档数（Edge Case 第一、二条） |
| `failed_count` | int | 处理过程本身出错的文档数及原因列表（FR-011） |
| `sources` | array[string] | 本次运行涉及的来源（`stackexchange`/`arxiv`） |

**存储位置**：不要求单独的 MongoDB 集合，可作为 Spark 作业运行结束后落盘的运行
统计（如 JSON 日志文件或 `mining_results` 之外的一个 `batches` 集合），只要满足
SC-003"1 分钟内可追溯"即可；具体落地形式留给 tasks.md 实施阶段决定。

## State Transitions

一条来源记录在本 feature 内的状态流转：

```text
原始记录（采集到）
  ├─ 缺必填字段 ────────────────→ 跳过（不写入 documents，计入 batch.skipped_count）
  ├─ 命中精确去重（source+source_url 已存在）
  │     └─ 内容更新 ─────────────→ upsert 覆盖 documents 中已有记录，产出新 MiningResult
  │     └─ 内容未变 ─────────────→ 不产生新记录（幂等，FR-012）
  ├─ 命中近似去重（dedup_hash 已存在于本批次） ─→ 跳过（计入 batch.skipped_count）
  └─ 通过校验与去重 ─────────────→ 写入/更新 documents
        └─ 进入 Spark 清洗/特征抽取
              ├─ 处理异常 ───────→ 记为失败（计入 batch.failed_count），不产出 MiningResult
              └─ 处理成功 ───────→ 写入 mining_results，并回填 documents.mining 摘要引用
```
