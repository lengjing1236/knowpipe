# Tasks: 个性化知识分类与 Web 展示

**Input**: Design documents from `/specs/002-personalized-web/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/api-contract.md](contracts/api-contract.md),
[quickstart.md](quickstart.md)

**Tests**: 项目宪法 Principle I/II 要求推荐分数有可检查证据、possible_conflict 有
可追溯判定依据，plan.md 已明确测试策略（契约测试 + mongomock 单测 + local[*]/真实
实例验收），因此本 tasks.md 包含测试任务。

**Organization**: 任务按用户故事分组，便于独立实现与验收。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 对应 spec.md 中的用户故事（US1/US2/US3）
- 描述中包含确切文件路径

## Path Conventions

在现有单一项目结构上新增子包，源码在 `knowpipe/web/`，测试在 `tests/web/`（见
plan.md Source Code 结构）；只读依赖 `knowpipe/mining/` 已产出的 `documents`/
`mining_results` 集合，不修改其结构，不导入其内部实现（仅通过 MongoDB 查询）。

---

## Phase 1: Setup（项目结构初始化）

**Purpose**: 按 plan.md 的 Source Code 结构创建 `knowpipe/web/` 子包骨架与
`tests/web/` 骨架，不含业务逻辑；声明新增依赖。

- [X] T001 创建 `knowpipe/web/__init__.py`（空包初始化文件）
- [X] T002 [P] 创建空的 `knowpipe/web/auth.py` 文件骨架（占位函数签名：密码哈希、
      session 登录校验、`@login_required` 装饰器）
- [X] T003 [P] 创建空的 `knowpipe/web/classify.py` 文件骨架（占位函数签名：单知识
      单元判定、按用户批量重新判定）
- [X] T004 [P] 创建空的 `knowpipe/web/score_job.py` 文件骨架（占位 `main()` 入口
      与命令行参数解析框架，对应 quickstart.md Step 5 的调用方式）
- [X] T005 [P] 创建空的 `knowpipe/web/baseline.py` 文件骨架（占位函数签名：纯相似度
      排序、最新优先降级）
- [X] T006 [P] 创建空的 `knowpipe/web/mongo_sink.py` 文件骨架（占位函数签名：
      users/user_profiles/user_knowledge 的读写、创建索引）
- [X] T007 [P] 创建空的 `knowpipe/web/app.py`、`knowpipe/web/routes_api.py`、
      `knowpipe/web/routes_pages.py` 文件骨架（app.py 占位应用工厂函数签名，
      routes_api.py/routes_pages.py 占位 Blueprint 定义）
- [X] T008 [P] 创建 `knowpipe/web/templates/login.html`、
      `knowpipe/web/templates/index.html` 骨架页面（含基本 HTML 结构，不含业务
      交互）与空的 `knowpipe/web/static/` 目录
- [X] T009 [P] 创建 `tests/web/__init__.py` 及空的测试文件骨架：
      `tests/web/test_auth.py`、`tests/web/test_classify.py`、
      `tests/web/test_score_job.py`、`tests/web/test_baseline.py`、
      `tests/web/test_mongo_sink.py`、`tests/web/test_routes_api.py`
- [X] T010 在 `requirements.txt` 中新增 `flask==3.1.2`（与本机已安装版本一致；
      plan.md Primary Dependencies 已列为依赖但此前未固定版本声明）
      → 已完成

**Checkpoint**: 子包骨架、测试骨架、依赖声明就位，可以开始填充基础实现。

---

## Phase 2: Foundational（阻塞性公共基础，三个用户故事都依赖）

**Purpose**: 对应 data-model.md 的三个新实体（User/UserProfile/UserKnowledge）与
research.md §1 的鉴权方式。这些不属于任何单一用户故事，是它们的共同前提。

**⚠️ CRITICAL**: 本阶段完成前，不能开始任何用户故事的实现任务。

- [X] T011 [P] 在 `knowpipe/web/mongo_sink.py` 实现 `users` 集合的写入
      （`user_id`/`username` 唯一索引创建、插入新用户、按 `username` 查询），
      字段定义见 data-model.md Entity: User
- [X] T012 [P] 在 `knowpipe/web/mongo_sink.py` 实现 `user_profiles` 集合的读写
      （`user_id` 唯一索引创建、按 `user_id` 查询/upsert），字段定义见
      data-model.md Entity: UserProfile
- [X] T013 [P] 在 `knowpipe/web/mongo_sink.py` 实现 `user_knowledge` 集合的读写
      （`user_id + knowledge_id` 复合唯一索引创建、批量 `update_many`/查询），
      字段定义见 data-model.md Entity: UserKnowledge
- [X] T014 在 `knowpipe/web/auth.py` 实现密码哈希与校验：包装 Werkzeug
      `generate_password_hash`/`check_password_hash`（对应 research.md §1），
      依赖 T011（需要写入/校验 `users.password_hash`）
- [X] T015 在 `knowpipe/web/auth.py` 实现登录态管理：写入/清除
      `session["user_id"]`，以及 `@login_required` 装饰器（校验 session 存在且
      对应用户在 `users` 集合中确实存在；对应 spec FR-004），依赖 T011, T014
- [X] T016 在 `knowpipe/web/auth.py` 实现越权校验辅助函数：比较请求体/查询参数
      中的 `user_id` 与 `session["user_id"]` 是否一致，不一致时返回 403（对应
      spec FR-005、contracts/api-contract.md 鉴权模型），依赖 T015
- [X] T017 [P] 在 `knowpipe/web/classify.py` 实现单知识单元判定规则：给定用户
      `known_keywords`/`known_topics` 与知识单元的关键词/所属主题簇，返回
      known/refine/new 三态之一（possible_conflict 由 T018 单独处理），对应
      data-model.md State Transitions，依赖 T012, T013
- [X] T018 在 `knowpipe/web/classify.py` 实现 possible_conflict 降级规则：无
      非空 `conflict_evidence` 或 `pending_review = true` 时，对外一律降级为
      new/refine（对应 spec FR-003、research.md §3），依赖 T017
- [X] T019 [P] `tests/web/test_auth.py`：覆盖 T014/T015/T016（密码哈希校验正确
      与错误密码、`@login_required` 拒绝无 session 请求、越权校验拒绝
      `user_id` 不一致的请求）
- [X] T020 [P] `tests/web/test_classify.py`：覆盖 T017/T018（关键词命中→known、
      主题簇命中但有新增内容→refine、均未命中→new、无证据的 possible_conflict
      降级、`pending_review=true` 时对外不可见）
- [X] T021 [P] `tests/web/test_mongo_sink.py`：覆盖 T011/T012/T013（唯一索引
      生效、`username` 重复注册失败、`user_knowledge` 复合索引下 upsert 不产生
      重复记录）

**Checkpoint**: 鉴权、三个新集合的读写、判定规则三块公共基础全部就位并通过单测，
User Story 1/2/3 可以开始实现。

---

## Phase 3: User Story 1 - 打通画像到页面的最小垂直切片（Priority: P1）🎯 MVP

**Goal**: 用 Feature 1 的 100+100 条切片数据，打通"注册/登录 → 画像录入 → 判定 →
查询接口 → 页面展示"全链路。

**Independent Test**: 使用 Feature 1 第一技术切片产出的 100+100 条挖掘结果作为
输入，注册并登录一个测试账户，设置若干已知主题，请求该用户的推荐结果，检查返回
的每条结果是否都带有判定状态与可读的判定依据（对应 spec User Story 1 的
Independent Test，验收步骤见 quickstart.md Step 1/2/3）。

### Tests for User Story 1 ⚠️

> 先写测试，确认失败，再实现。

- [X] T022 [P] [US1] `tests/web/test_routes_api.py`：覆盖 `POST /api/auth/register`
      （成功 201、用户名已存在 409、字段缺失 400）与 `POST /api/auth/login`
      （成功 200 并设置 session、密码错误 401），对应 contracts/api-contract.md
      Contract 1/2
- [X] T023 [P] [US1] `tests/web/test_routes_api.py`：覆盖 `GET /api/topics`
      （公开访问 200，按 `document_count` 降序）与
      `GET /api/documents/{source}/{doc_id}`（公开访问 200、不存在返回 404），
      对应 Contract 4/5
- [X] T024 [P] [US1] `tests/web/test_routes_api.py`：覆盖
      `GET /api/recommendations`（未登录 401、已登录但 `user_id` 不一致 403、
      `limit` 非正整数 400、正常请求 200 且每条 `items[]` 含非空 `status`），
      对应 Contract 6、spec Edge Cases 未登录/越权/无效分页三条
- [X] T025 [P] [US1] `tests/web/test_routes_api.py`：覆盖
      `POST /api/profile/topics`（未登录 401、`user_id` 不一致 403、无效主题簇
      标识 400、成功 200 并返回更新后的 `known_topics`），对应 Contract 7

### Implementation for User Story 1

- [X] T026 [US1] 在 `knowpipe/web/mongo_sink.py` 实现从 `mining_results` 聚合
      每个主题簇的代表关键词与 `document_count`（供 `/api/topics` 使用，
      data-model.md Assumptions 未固定聚合方式，取簇内 TF-IDF 权重最高的若干
      关键词作为默认实现），依赖 T013
- [X] T027 [US1] 在 `knowpipe/web/mongo_sink.py` 实现 `known_topics`/
      `read_doc_ids` 变化时重新派生 `known_keywords` 的聚合逻辑（对应
      data-model.md UserProfile 派生规则：从对应主题簇/已读文档的挖掘结果中
      聚合高权重关键词），依赖 T012, T026
- [X] T028 [US1] 在 `knowpipe/web/routes_api.py` 实现
      `POST /api/auth/register`：校验字段非空与密码长度阈值，调用 T011/T014
      写入新用户，依赖 T011, T014
- [X] T029 [US1] 在 `knowpipe/web/routes_api.py` 实现 `POST /api/auth/login`
      与 `POST /api/auth/logout`：校验凭据、写入/清除 session（依赖 T015），
      依赖 T011, T014, T015
- [X] T030 [US1] 在 `knowpipe/web/routes_api.py` 实现 `GET /api/topics`（公开，
      调用 T026）与 `GET /api/documents/{source}/{doc_id}`（公开，只读查询
      Feature 1 的 `documents`/`mining_results`，不存在返回 404），依赖 T026
- [X] T031 [US1] 在 `knowpipe/web/routes_api.py` 实现
      `GET /api/recommendations`：校验登录态与越权（依赖 T015/T016）、校验
      `limit`/`source`/`mode` 参数、`mode=personalized` 时查询 T013 的
      `user_knowledge` 记录并按 T017/T018 的判定结果组装响应，依赖 T015, T016,
      T017, T018, T013
- [X] T032 [US1] 在 `knowpipe/web/routes_api.py` 实现 `POST /api/profile/topics`：
      校验登录态与越权、校验 `known_topics` 内的主题簇标识是否存在于 T026 的
      结果范围内，更新 `UserProfile.known_topics`（触发 T027 重新派生
      `known_keywords`），并同步触发该用户名下 `user_knowledge` 记录按 T017/T018
      重新判定（对应 research.md §5、spec SC-004），依赖 T027, T017, T018, T012
- [X] T033 [US1] 在 `knowpipe/web/app.py` 实现 Flask 应用工厂：注册
      `routes_api.py`/`routes_pages.py` 的 Blueprint，配置 session 密钥、
      MongoDB 连接初始化，依赖 T028-T032
- [X] T034 [US1] 在 `knowpipe/web/routes_pages.py` 与
      `knowpipe/web/templates/login.html`/`index.html` 实现最小可用页面：
      登录/注册表单、已知主题选择控件、推荐列表展示（来源文档、涉及关键词、
      判定依据），页面通过原生 JS `fetch` 调用 T028-T032 的 API（对应 spec
      FR-012、SC-006），依赖 T033
- [X] T035 [US1] 按 quickstart.md Step 1/2/3 手动执行一次验收：注册登录、设置
      已知主题、请求推荐结果确认判定依据非空、新用户默认全 new、未登录 401、
      越权 403（对应 spec SC-001、FR-013），依赖 T034

**Checkpoint**: 此时 User Story 1 已可独立验收交付——最小垂直切片打通，且不
依赖 User Story 2/3 的任何任务。

---

## Phase 4: User Story 2 - 规模化判定与效果评价（Priority: P2）

**Goal**: 将链路从 100+100 条样例扩展到 Feature 1 全量规模（≥10,000 条），批量
完成个性化判定，并产出个性化推荐与非个性化基线的对比数据。

**Independent Test**: 对 Feature 1 产出的全量规模挖掘结果，批量完成个性化判定，
构造至少两个不同的模拟用户画像，人工标注推荐结果的相关性，计算个性化排序与
非个性化基线之间的对比指标（对应 spec User Story 2 的 Independent Test，验收
步骤见 quickstart.md Step 5）。

### Tests for User Story 2 ⚠️

- [X] T036 [P] [US2] `tests/web/test_score_job.py`：用
      `SparkSession.builder.master("local[*]")` 起本地会话，构造样例文档与
      `mining_results`，断言 `score_job.py` 产出的每条记录 `score` 为
      `α×主题相关度 + β×新增关键词比例 + γ×文档质量 - ε×已读/重复惩罚`
      公式的合理范围内数值，且写入的 `batch_id` 可在 `batches` 集合查到运行
      统计（复用 `knowpipe/mining/batch.py`）
- [X] T037 [P] [US2] `tests/web/test_baseline.py`：覆盖有已读文档时按
      `similar_doc_ids` 相似度排序、无已读文档时退化为 `created_at` 倒序两种
      路径（对应 research.md §4）
- [X] T038 [P] [US2] `tests/web/test_routes_api.py` 新增用例：
      `GET /api/recommendations?mode=baseline` 返回条目不含
      `status`/`matched_keywords`/`matched_topic_cluster_id` 字段（对应
      Contract 6 baseline 分支）

### Implementation for User Story 2

- [X] T039 [US2] 在 `knowpipe/web/score_job.py` 实现推荐分数计算：复用
      `knowpipe/mining/spark_job.py` 的 `local[*]` 会话模式，只读查询
      `documents`/`mining_results`，按 research.md §2 公式（不含来源多样性 δ
      项）计算每条文档的 `score`，写入 `user_knowledge.score`，依赖 T013
- [X] T040 [US2] 在 `knowpipe/web/score_job.py` 中调用
      `knowpipe/mining/batch.py` 的 `new_batch_id`/`BatchStats` 记录本次运行
      统计（`started_at`/`finished_at`/`input_count`/`failed_count` 等），
      写入与 Feature 1 相同的 `batches` 集合，依赖 T039
- [X] T041 [US2] 在 `knowpipe/web/score_job.py` 组装命令行入口（对应
      quickstart.md Step 5 的调用方式：`--mongo-uri`/`--mongo-db` 参数），
      依赖 T039, T040
- [X] T042 [US2] 在 `knowpipe/web/baseline.py` 实现非个性化基线排序：用户有
      `read_doc_ids` 时取其 `similar_doc_ids` 关联文档按相似度排序，否则按
      `created_at` 倒序（对应 research.md §4 Decision），依赖 T012
- [X] T043 [US2] 在 `knowpipe/web/routes_api.py` 的 `GET /api/recommendations`
      中接入 `mode=baseline` 分支：调用 T042，响应不含个性化判定字段（对应
      Contract 6），依赖 T031, T042
- [ ] T044 [US2] 按 quickstart.md Step 5 手动执行验收：在 Feature 1 全量规模
      （≥10,000 条）数据上运行 `score_job.py`，确认 `batch_id` 可在 1 分钟内
      查询到运行统计；构造至少两个模拟用户画像（对应真实注册账户），人工标注
      `mode=personalized` 与 `mode=baseline` 两组结果的相关性，计算 Precision@K
      或等价指标（对应 spec SC-002/SC-003、FR-014/015/016），依赖 T041, T043

**Checkpoint**: 此时 User Story 1 与 User Story 2 均可独立验收交付——最小切片
与规模化评价分别成立，互不依赖对方的额外任务即可分别演示。

---

## Phase 5: User Story 3 - 反馈闭环与画像演进（Priority: P3）

**Goal**: 用户提交反馈后，系统更新画像并使相关知识单元的判定状态随之调整。

**Independent Test**: 对一个已有画像和历史推荐记录的用户提交一条反馈，重新请求
该用户的推荐列表，检查对应知识单元及其相关内容的判定状态是否发生了预期变化
（对应 spec User Story 3 的 Independent Test，验收步骤见 quickstart.md Step 4）。

### Tests for User Story 3 ⚠️

- [X] T045 [P] [US3] `tests/web/test_routes_api.py` 新增用例：
      `POST /api/profile/feedback`（未登录 401、`user_id` 不一致 403、`action`
      不在允许枚举值内 400、成功 200 并返回反馈记录），对应 Contract 8
- [X] T046 [P] [US3] `tests/web/test_classify.py` 新增用例：同一 `knowledge_id`
      先后提交矛盾反馈时，画像以最近一次为准更新，不保留旧反馈造成的矛盾状态
      （对应 spec FR-019、Acceptance Scenario 2）

### Implementation for User Story 3

- [X] T047 [US3] 在 `knowpipe/web/routes_api.py` 实现
      `POST /api/profile/feedback`：校验登录态与越权、校验 `action` 枚举值，
      追加 `UserProfile.feedback_history` 记录，依赖 T015, T016, T012
- [X] T048 [US3] 在 `knowpipe/web/mongo_sink.py`/`classify.py` 中实现
      `action = confirmed_known` 时的画像更新：将对应关键词/主题簇纳入
      `known_keywords`/`known_topics`（复用 T027 的派生逻辑），并触发该用户
      相关知识单元按 T017/T018 重新判定（对应 Contract 8 副作用说明、spec
      FR-009/017/019），依赖 T047, T027, T017, T018
- [X] T049 [US3] 按 quickstart.md Step 4 手动执行验收：对某条 `status=new` 的
      `knowledge_id` 提交 `confirmed_known` 反馈，重新请求推荐结果，确认该
      知识单元及关联同一关键词/主题簇的其他条目状态变为 `known` 或 `refine`
      （对应 spec SC-004），依赖 T048

**Checkpoint**: 三个用户故事均可独立验收交付，且互不依赖。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 影响多个用户故事的收尾工作。

- [X] T050 [P] 检查 `knowpipe/web/` 各模块是否只通过读查询访问
      `knowpipe/mining/` 的 `documents`/`mining_results`，未导入其内部实现、
      未写入其集合（对应 Feature 1 storage-contract.md"下游只读消费"边界与
      plan.md Structure Decision）
- [X] T051 [P] 补全 `knowpipe/web/` 各模块的运行时错误信息与日志输出（登录
      失败原因、越权拒绝原因、`score_job.py` 失败时的批次统计写入），参考
      Feature 1 T043 的错误处理修复模式
- [X] T052 汇总一次完整的端到端验收记录（quickstart.md 全部 Step），更新到
      `evidence/002-personalized-web/acceptance-record.md`，作为课程答辩证据
      索引，格式参考
      [`evidence/001-data-pipeline-spark-mining/acceptance-record.md`](../../evidence/001-data-pipeline-spark-mining/acceptance-record.md)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（Setup）**：无前置依赖，可以立即开始
- **Phase 2（Foundational）**：依赖 Phase 1 完成，阻塞所有用户故事
- **Phase 3/4/5（User Story 1/2/3）**：均依赖 Phase 2 完成；User Story 2 依赖
  User Story 1 的 `routes_api.py`/`mongo_sink.py` 主体实现（T031/T033）已经
  跑通（`mode=baseline` 是在已有的 `/api/recommendations` 上新增分支），因此
  实际执行顺序是 US1 → US2；User Story 3 依赖 User Story 1 的 T027（
  `known_keywords` 派生逻辑已实现）与 T032（画像更新触发重新判定的模式已
  建立），可以在 US1 完成后与 US2 并行开展
- **Phase 6（Polish）**：依赖期望完成的用户故事全部完成

### Parallel Opportunities

- Phase 1 中 T002-T009 可并行（不同文件的骨架创建）
- Phase 2 中 T011/T012/T013/T017（不同文件或独立字段）可并行；T014/T015/T016
  因同文件依赖需按顺序；T019/T020/T021（测试）可并行
- Phase 3 中 T022-T025（测试）可并行；T026/T027 因同文件字段依赖需按顺序
- Phase 4 中 T036/T037/T038（测试）可并行
- Phase 5 中 T045/T046（测试）可并行，且可与 Phase 4 的任务并行（不同用户
  故事、不同验收目标）

---

## Parallel Example: User Story 1

```bash
# 先并行写测试：
Task: "tests/web/test_routes_api.py — register/login 契约测试"
Task: "tests/web/test_routes_api.py — topics/document-detail 契约测试"
Task: "tests/web/test_routes_api.py — recommendations 鉴权/越权/参数校验测试"
Task: "tests/web/test_routes_api.py — profile/topics 契约测试"

# 测试确认失败后再实现：
Task: "knowpipe/web/routes_api.py — register/login/logout 路由"
Task: "knowpipe/web/routes_api.py — topics/document-detail 只读路由"
```

---

## Implementation Strategy

### MVP First（User Story 1）

1. 完成 Phase 1：Setup
2. 完成 Phase 2：Foundational（鉴权、三个新集合读写、判定规则）
3. 完成 Phase 3：User Story 1
4. **停下并验收**：按 quickstart.md Step 1/2/3 独立验证 User Story 1
5. 此时已构成课程答辩的最小可演示版本（100+100 切片全链路打通，含注册登录）

### Incremental Delivery

1. Phase 1 + 2 完成 → 基础就位
2. 加入 User Story 1 → 独立验收 → 可演示（MVP）
3. 加入 User Story 2 → 独立验收 → 规模化证据与基线对比数据到位
4. 加入 User Story 3 → 独立验收 → 反馈闭环演示到位
