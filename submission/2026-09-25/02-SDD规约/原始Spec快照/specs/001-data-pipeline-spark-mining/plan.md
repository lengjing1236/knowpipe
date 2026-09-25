# Implementation Plan: 数据管道与 Spark 挖掘

**Branch**: `001-data-pipeline-spark-mining` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-data-pipeline-spark-mining/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

从 Stack Exchange（主源）与 arXiv CS（补充源）采集原始数据，统一为文档结构；用 PySpark
批处理完成清洗、去重、TF-IDF 关键词抽取、KMeans 主题聚类与文档相似度计算；结果以
`source+doc_id` 幂等 upsert 写入 MongoDB 的 `documents` 与 `mining_results` 两个集合，
每次运行携带唯一 `batch_id` 并留存运行统计作为课程证据。技术选型直接沿用《课程项目
选题调研与实施方案.md》已确定的决策，本计划不重新论证选型，只落地到可执行结构。

## Technical Context

**Language/Version**: Python 3.10（与现有 Knowpipe 仓库一致）

**Primary Dependencies**: PySpark（DataFrame + MLlib，用于 TF-IDF/KMeans/相似度）、
PyMongo（MongoDB 客户端与 upsert）、requests（Stack Exchange REST API 与 arXiv API 采集）

**Storage**: MongoDB，集合 `documents`（唯一索引 `source+doc_id`）与 `mining_results`
（索引 `doc_id+batch_id`），字段结构见调研文档第 5.2/5.4 节

**Testing**: unittest（与仓库现有 `tests/test_core.py` 一致）；Spark 相关逻辑用
`SparkSession.builder.master("local[*]")` 起本地会话做单元/集成测试；MongoDB 相关逻辑
优先用 `mongomock` 做无需真实实例的单元测试，真实 MongoDB 实例仅用于规模化运行验证
（对应 spec User Story 2）

**Target Platform**: Linux（课程提供的 Spark 集群或经教师同意的本机 `local[*]` 备用
路径，见调研文档第 4 节）

**Project Type**: 批处理数据管道（单一 Python 项目内的新增子模块，非独立服务）

**Performance Goals**: 无严格的延迟指标（批处理离线运行）；以"两源各 100 条可在数分钟
内跑通全链路"作为最小切片的可用性目标，规模化运行（≥10,000 条）以"可无人工干预完整
执行完毕"为目标，具体耗时不作为验收标准

**Constraints**: 核心计算必须由 Spark 产出且有可检查执行证据（项目宪法 Principle I）；
禁止用 Python 线程池替代 Spark、禁止用 JSONL/SQLite 替代 MongoDB（项目宪法 Principle
III）；单条文档失败不得中断整批运行（spec FR-011）

**Scale/Scope**: 最小切片 200 条文档（两源各 100）；最终规模不少于 10,000 条有效
Stack Exchange 文档及 arXiv 补充数据（spec FR-008）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法原则 | 检查项 | 结果 |
|---|---|---|
| I. Spark 核心计算边界 | 清洗/去重/TF-IDF/KMeans/相似度是否全部由 Spark DataFrame/MLlib 产出，而非 LLM 或临时脚本 | PASS — 本 feature 不涉及 LLM，全部计算落在 PySpark |
| II. 知识单元级分类与可追溯性 | 本 feature 是否越权做了个性化分类判定 | PASS — 本 feature 只产出关键词/主题簇等中间结果，known/refine/new 判定属于下游 feature，见 spec Assumptions |
| III. 技术选型不可替代边界 | 是否用线程池冒充 Spark、JSONL/SQLite 冒充 MongoDB | PASS — 显式使用 PySpark 与真实 MongoDB；测试环境用 `local[*]` 与 `mongomock` 仅用于单元测试替身，规模化验证仍需真实实例（见 Testing 与 research.md 风险项） |
| IV. 数据规模基线 | 是否设计了"先 100 条打通、再扩展到 ≥10,000 条"的两阶段验证 | PASS — 对应 spec User Story 1/2 与 FR-008 |
| V. 可评价性 | 本 feature 不产出推荐排序，是否误引入评价指标 | PASS — 评价指标（Precision@K/NDCG）属于下游个性化/推荐 feature，本 feature 不涉及 |
| VI. 文档状态标记约定 | plan/research 是否区分已验证与待验证内容 | PASS — research.md 将本机 PySpark/MongoDB 环境未验证列为待验证风险项 |

无违规项，Complexity Tracking 章节留空。

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
knowpipe/
├── mining/                      # 本 feature 新增子模块，与现有 knowpipe/ 包并列
│   ├── __init__.py
│   ├── collectors/
│   │   ├── stackexchange.py     # Stack Exchange API 采集，统一为原始记录
│   │   └── arxiv.py             # arXiv API 采集，统一为原始记录
│   ├── contract.py               # 统一文档字段结构（doc_id/source/.../batch_id/mining）与校验
│   ├── dedup.py                  # 去重规则（source+source_url 或内容哈希），供 Spark 作业调用
│   ├── spark_job.py               # Spark 入口：清洗 → 去重 → TF-IDF → KMeans → 相似度
│   ├── mongo_sink.py               # MongoDB upsert 写入 documents / mining_results，索引创建
│   └── batch.py                    # batch_id 生成与运行统计记录（起止时间/条数/失败数）

tests/
└── mining/
    ├── test_contract.py          # 统一字段结构与必填字段校验
    ├── test_dedup.py              # 去重规则单测
    ├── test_spark_job.py           # 用 local[*] SparkSession 跑最小样例，校验关键词/簇非空
    └── test_mongo_sink.py           # 用 mongomock 校验 upsert 幂等性与索引声明
```

**Structure Decision**: 采用单一 Python 项目内新增子包（`knowpipe/mining/`），与现有
`knowpipe/` 包（brain.py/store.py/cli.py 等）并列，不引入独立服务或前后端分离结构——
本 feature 是离线批处理管道，不对外暴露接口（对外接口属于下游"Web API" feature）。
复用现有仓库的 HTML 清洗/文本处理经验（如 `knowpipe/ingest.py` 的清洗思路）作为
`mining/contract.py` 里正文清洗的参考，但不直接依赖现有模块的线程池/JSONL 实现，
遵守项目宪法"现有 Knowpipe 复用边界"一节的复用限制。

## Complexity Tracking

无宪法违规项，本节留空。
