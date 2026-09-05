# Implementation Plan: 个性化知识分类与 Web 展示

**Branch**: `002-personalized-web` | **Date**: 2026-09-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-personalized-web/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

基于 Feature 1 产出的 `documents`/`mining_results`，为知识单元（关键词/主题簇）判定
known/refine/new/possible_conflict 四态并计算推荐排序分数，写入新增的 `user_knowledge`
集合；新增 `users`（登录凭据）与 `user_profiles`（已知主题/已读文档/反馈）集合；
提供 Flask 鉴权 + 只读/画像 API，以及一个消费这些 API 的单页 Web。分类判定依据
Spark 已产出的关键词/主题簇做规则匹配（不重新计算 TF-IDF/KMeans），但推荐排序分数
按项目宪法 Principle I 仍由 Spark 产出，两者在同一批处理作业中一并写入并共享
`batch_id`，与 Feature 1 的批次证据模式保持一致。

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.10（与现有仓库一致）

**Primary Dependencies**: Flask（API 路由 + 单页 Web 服务端渲染）、Werkzeug（Flask
自带，用于密码哈希 `generate_password_hash`/`check_password_hash` 与 session 签名，
不引入新依赖）、PySpark（复用 Feature 1 的 `SparkSession.builder.master("local[*]")`
模式，仅用于推荐排序分数计算）、PyMongo（读写 `users`/`user_profiles`/`user_knowledge`，
只读查询 Feature 1 的 `documents`/`mining_results`）

**Storage**: MongoDB，新增集合 `users`（`user_id` 唯一索引，登录凭据）、
`user_profiles`（`user_id` 唯一索引，已知主题/已读文档/反馈）、`user_knowledge`
（`user_id + knowledge_id` 复合索引，判定状态/依据/推荐分数/产出批次标识）；只读
依赖 Feature 1 已建成的 `documents`/`mining_results` 集合，不修改其结构

**Testing**: unittest（与仓库一致）；Flask 路由用 `app.test_client()` 做契约测试；
MongoDB 相关逻辑优先用 `mongomock`；Spark 推荐分数计算复用 Feature 1 的
`local[*]` 单测模式

**Target Platform**: Linux 本机，Flask 开发服务器（`flask run` 或 `app.run()`）用于
课程演示，不要求生产级 WSGI 部署

**Project Type**: Web 应用——Flask 后端 API + 服务端渲染单页（Jinja2 模板 + 原生
JS `fetch` 调用 API），非前后端分离的 SPA 框架，与调研文档 5.5 节"单页 Web"及
"图表失败则用原生页面"降级方向一致

**Performance Goals**: 无严格延迟指标（课程演示场景，单人或几人访问）；用户修改
画像或提交反馈后，触发的单用户范围重算需在数秒内返回，使演示交互可用

**Constraints**: 登录鉴权必须覆盖用户个人数据（画像/推荐/反馈）的全部读写路径
（spec FR-004/005）；`possible_conflict` 无可追溯证据必须降级为 `new`/`refine`
（spec FR-003）；推荐排序分数必须由 Spark 产出且留有可检查证据（项目宪法
Principle I，NON-NEGOTIABLE）；认证方案不要求生产级安全合规（无需 MFA、完整
密码找回流程）

**Scale/Scope**: 最小切片沿用 Feature 1 第一技术切片产出的 100+100 条挖掘结果与
至少 1 个真实注册用户账户；规模化验证覆盖 Feature 1 全量规模（≥10,000 条）与至少
2 个模拟用户画像（spec FR-012/015）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法原则 | 检查项 | 结果 |
|---|---|---|
| I. Spark 核心计算边界 | 推荐排序分数是否由 Spark 产出且可检查；分类判定是否误用 LLM 替代核心计算 | PASS — `score_job.py` 用 PySpark 计算推荐分数并留 `batch_id`/运行统计证据；known/refine/new/possible_conflict 判定只是对 Spark 已产出的关键词/主题簇做集合匹配，不属于 TF-IDF/KMeans/相似度/分数四类核心计算之一，不强制要求用 Spark 重新实现，但为共享批次证据，判定逻辑与分数计算被安排在同一个 Spark 作业里一并产出（见 research.md 决策） |
| II. 知识单元级分类与可追溯性 | 判定粒度是否落在知识单元；`possible_conflict` 是否有证据与人工复核门槛 | PASS — 判定对象是关键词/主题簇（spec FR-001），`user_knowledge` 记录命中依据字段；`possible_conflict` 无证据时按 FR-003 强制降级 |
| III. 技术选型不可替代边界 | 是否用 SQLite/JSONL 冒充 MongoDB，或用非 Web 形式冒充页面 | PASS — 显式使用 MongoDB 与 Flask + HTML 页面；`mongomock` 仅作单测替身，规模化验证仍需真实 MongoDB 实例 |
| IV. 数据规模基线 | 是否先用 100+100 切片打通全链路再扩展到 ≥10,000 | PASS — 对应 spec User Story 1/2 与 FR-015 |
| V. 可评价性 | 是否设置非个性化基线并用可复现指标对比 | PASS — `baseline.py` 提供热门度/相似度基线，FR-014/016 要求计算并展示对比指标 |
| VI. 文档状态标记约定 | plan/research 是否区分已验证与待验证内容 | PASS — research.md 将"单用户范围 Spark 重算的实际耗时"列为待验证项 |

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
├── mining/                       # Feature 1 已有子模块，本 feature 只读依赖，不修改
│
└── web/                          # 本 feature 新增子模块，与 mining/ 并列
    ├── __init__.py
    ├── auth.py                    # 密码哈希、session 登录校验、@login_required 装饰器
    ├── classify.py                 # known/refine/new/possible_conflict 判定规则
    ├── score_job.py                 # Spark 入口：推荐排序分数计算（复用 mining/spark_job.py 的会话模式）
    ├── baseline.py                  # 非个性化基线排序（热门度/纯相似度），供效果对比
    ├── mongo_sink.py                  # users/user_profiles/user_knowledge 的读写与索引创建
    ├── app.py                          # Flask 应用工厂 + 全部路由注册
    ├── routes_api.py                    # /api/* 路由处理函数
    ├── routes_pages.py                   # 登录/注册/单页 Web 的页面路由
    ├── templates/                         # Jinja2 模板（登录页、单页 Web）
    │   ├── login.html
    │   └── index.html
    └── static/                             # 单页 Web 的原生 JS/CSS

tests/
└── web/
    ├── test_auth.py                # 密码哈希、登录校验、越权访问拒绝
    ├── test_classify.py             # 四态判定规则、possible_conflict 降级逻辑
    ├── test_score_job.py             # 用 local[*] SparkSession 跑最小样例，校验分数产出与 batch_id
    ├── test_baseline.py               # 非个性化基线排序正确性
    ├── test_mongo_sink.py              # 用 mongomock 校验 upsert 幂等性与索引声明
    └── test_routes_api.py               # 用 Flask test_client 做 API 契约测试（含未登录/越权拒绝）
```

**Structure Decision**: 采用单一 Python 项目内新增子包（`knowpipe/web/`），与
`knowpipe/mining/`（Feature 1）并列，不引入独立前端框架或前后端分离仓库——单页
Web 由 Flask 服务端渲染 + 原生 JS 调用同进程 API，符合调研文档 5.5 节的最小实施
方案与"图表失败则用原生页面"的降级方向。`knowpipe/web/` 只通过读查询访问
`knowpipe/mining/` 产出的 `documents`/`mining_results`，不导入其内部实现，保持
两个 feature 的存储契约边界（对应 001 的 storage-contract.md）。

## Complexity Tracking

无宪法违规项，本节留空。
