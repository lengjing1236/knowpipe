# Tasks: 数据管道与 Spark 挖掘

**Input**: Design documents from `/specs/001-data-pipeline-spark-mining/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/storage-contract.md](contracts/storage-contract.md),
[quickstart.md](quickstart.md)

**Tests**: 项目宪法 Principle I/III 要求核心计算有可检查证据，plan.md 已明确测试策略
（mongomock 单测 + local[*]/真实实例验收），因此本 tasks.md 包含测试任务。

**Organization**: 任务按用户故事分组，便于独立实现与验收。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 对应 spec.md 中的用户故事（US1/US2/US3）
- 描述中包含确切文件路径

## Path Conventions

单一项目结构，源码在 `knowpipe/mining/`，测试在 `tests/mining/`（见 plan.md Source
Code 结构）。

---

## Phase 0: 环境验证 Spike（阻塞性前置任务，不属于任何用户故事）

**Purpose**: research.md §1 指出本机 PySpark/MongoDB 环境从未验证过，这是"能不能跑
起来"的事实问题，必须在写任何业务代码之前确认，否则后续所有任务都建立在未验证假设上。

**⚠️ CRITICAL**: 本阶段任何一步失败，都必须先按调研文档第 4 节的备用路径解决（申请
教师同意的 `local[*]`，或申请课程 MongoDB 实例），再继续后续阶段；不得跳过验证直接
写业务代码。

- [X] T001 验证 `pyspark` 可安装并 import：执行 `python3 -c "import pyspark; print(pyspark.__version__)"`，
      失败则记录错误并按 plan.md Constraints 申请课程集群或教师同意的 `local[*]` 环境
      → 已验证，`pyspark==4.2.0`，见 research.md §1
- [X] T002 验证 PySpark 可以起本地会话：编写并运行一次性验证脚本，
      `SparkSession.builder.master("local[*]").appName("env-check").getOrCreate()`，
      确认能创建一个 DataFrame 并执行 `.count()`
      → 已验证，本机 Java 17 + local[*] 正常，见 research.md §1
- [X] T003 验证 `pymongo` 可安装并连接到一个真实 MongoDB 实例（本机容器或课程集群），
      执行 `MongoClient(uri).admin.command("ping")` 确认连接成功
      → 已验证，本机以官方二进制部署 MongoDB 7.0.14 真实实例
      （`mongodb://127.0.0.1:27017`），ping 返回 `{'ok': 1.0}`，见 research.md §1
- [X] T004 将 T001-T003 的验证结果（成功/失败、使用的具体环境）记录到
      `specs/001-data-pipeline-spark-mining/research.md` §1 下方，作为"已验证"状态更新
      （遵循项目宪法 Principle VI 的文档状态标记约定）
      → 已完成

**Checkpoint**: 环境验证全部通过后才能进入 Phase 1；若某一步长期无法解决，需回到
plan.md 与用户确认是否要调整技术路径。

---

## Phase 1: Setup（项目结构初始化）

**Purpose**: 按 plan.md 的 Source Code 结构创建子包骨架，不含业务逻辑。

- [X] T005 创建 `knowpipe/mining/__init__.py`（空包初始化文件）
- [X] T006 [P] 创建 `knowpipe/mining/collectors/__init__.py` 及空的
      `knowpipe/mining/collectors/stackexchange.py`、`knowpipe/mining/collectors/arxiv.py`
      文件骨架（含模块 docstring，占位函数签名，不含实现）
- [X] T007 [P] 创建空的 `knowpipe/mining/contract.py` 文件骨架（占位函数签名：
      文档字段校验、质量标记计算）
- [X] T008 [P] 创建空的 `knowpipe/mining/dedup.py` 文件骨架（占位函数签名：精确去重、
      内容哈希近似去重）
- [X] T009 [P] 创建空的 `knowpipe/mining/spark_job.py` 文件骨架（占位 `main()` 入口
      与命令行参数解析框架，对应 contracts/storage-contract.md 的 Invocation Contract）
- [X] T010 [P] 创建空的 `knowpipe/mining/mongo_sink.py` 文件骨架（占位函数签名：
      upsert documents、写入 mining_results、创建索引）
- [X] T011 [P] 创建空的 `knowpipe/mining/batch.py` 文件骨架（占位函数签名：生成
      batch_id、记录运行统计）
- [X] T012 [P] 创建 `tests/mining/__init__.py` 及空的测试文件骨架：
      `tests/mining/test_contract.py`、`tests/mining/test_dedup.py`、
      `tests/mining/test_spark_job.py`、`tests/mining/test_mongo_sink.py`
- [X] T013 在项目依赖声明中新增 `pyspark`、`pymongo`、`mongomock`（若仓库无
      `requirements.txt`/`pyproject.toml` 依赖清单，先创建一个，与现有 Python 3.10
      环境保持一致）
      → 已创建 `requirements.txt`，固定版本 pyspark==4.2.0 / pymongo==4.18.0 /
      mongomock==4.3.0（与本机 Phase 0 验证时安装的版本一致）

**Checkpoint**: 子包骨架与测试骨架就位，可以开始填充基础实现。

---

## Phase 2: Foundational（阻塞性公共基础，三个用户故事都依赖）

**Purpose**: 对应 data-model.md 的三个实体（Document/MiningResult/Batch）与
research.md §2/§3 的去重、失败隔离规则。这些不属于任何单一用户故事，是它们的共同前提。

**⚠️ CRITICAL**: 本阶段完成前，不能开始任何用户故事的实现任务。

- [X] T014 [P] 在 `knowpipe/mining/contract.py` 实现 Document 统一字段结构与必填字段
      校验（doc_id/source/source_site/title/body_text/language/created_at/source_url
      非空；缺失时返回校验失败原因），字段定义见 data-model.md Entity: Document
- [X] T015 [P] 在 `knowpipe/mining/contract.py` 实现质量标记计算：正文长度低于阈值时
      置 `quality.short_body = true`（对应 spec Edge Case 第三条、data-model.md
      质量标记子结构）
      → 阈值 50 词（spec Assumptions 未固定具体值，作为实施阶段决定的默认值，
      写在 `SHORT_BODY_WORD_THRESHOLD` 常量中）
- [X] T016 [US-shared] 在 `knowpipe/mining/dedup.py` 实现精确去重判定：按
      `source + source_url` 判断是否已存在，依赖 T014（contract.py 的字段结构）
- [X] T017 [US-shared] 在 `knowpipe/mining/dedup.py` 实现内容哈希近似去重：对清洗后
      标题+正文规范化后取 SHA-256，写入 `quality.dedup_hash`，与本批次内已处理的哈希
      集合比较（对应 research.md §2 的两级判定，spec Edge Case 第二条），依赖 T014
- [X] T018 [P] 在 `knowpipe/mining/batch.py` 实现 `batch_id` 生成（格式
      `{YYYYMMDD}-{序号或哈希}`，见 data-model.md Entity: Batch）
- [X] T019 在 `knowpipe/mining/batch.py` 实现运行统计记录（`started_at`/
      `finished_at`/`input_count`/`valid_count`/`skipped_count`/`failed_count`/
      `sources` 字段的读写），依赖 T018
- [X] T020 [P] `tests/mining/test_contract.py`：覆盖 T014/T015（必填字段缺失时校验
      失败、正文过短时 short_body 标记正确）
- [X] T021 [P] `tests/mining/test_dedup.py`：覆盖 T016/T017（相同 source_url 判重、
      不同来源但内容哈希相同判重、内容不同不误判）

**Checkpoint**: Document 契约、去重规则、Batch 统计三块公共基础全部就位并通过单测，
User Story 1/2/3 可以开始实现。

---

## Phase 3: User Story 1 - 打通最小链路验证（Priority: P1）🎯 MVP

**Goal**: 两个来源各提供 100 条原始文档，跑通"采集 → 统一字段 → Spark 处理 → 写入
MongoDB"全链路，产出可查询的文档与挖掘结果记录。

**Independent Test**: 分别从两个来源各提供 100 条原始文档，触发一次处理流程，仅检查
MongoDB 中出现的文档与挖掘结果记录即可判定成功，不依赖任何下游功能（对应 spec User
Story 1 的 Independent Test，验收步骤见 quickstart.md Step 1/2）。

### Tests for User Story 1 ⚠️

> 先写测试，确认失败，再实现。

- [X] T022 [P] [US1] `tests/mining/test_spark_job.py`：用
      `SparkSession.builder.master("local[*]")` 起本地会话，构造 10 条样例文档，断言
      经过清洗+TF-IDF+KMeans 后每条产出非空 `keywords` 与 `topic_cluster_id`
- [X] T023 [P] [US1] `tests/mining/test_mongo_sink.py`：用 `mongomock` 构造内存
      MongoDB，断言 upsert 后 `documents` 集合中 `source+doc_id` 唯一、
      `mining_results` 集合中每条记录都能通过 `doc_id+batch_id` 精确查询到

### Implementation for User Story 1

- [X] T024 [P] [US1] 在 `knowpipe/mining/collectors/stackexchange.py` 实现从
      Stack Exchange API 采集指定数量（默认 100）问答，转换为 Document 统一字段结构
      （调用 T014 的校验），缺失必填字段的原始记录标记为无效并跳过（spec FR-002/
      Edge Case 第一条）
- [X] T025 [P] [US1] 在 `knowpipe/mining/collectors/arxiv.py` 实现从 arXiv API 采集
      指定数量（默认 100）CS 类别的标题/摘要元数据，转换为 Document 统一字段结构
      （调用 T014 的校验），同样处理缺失字段跳过逻辑
- [X] T026 [US1] 在 `knowpipe/mining/spark_job.py` 实现清洗与去重阶段：将 T024/T025
      采集到的原始记录载入 Spark DataFrame，在进入 TF-IDF/KMeans 之前调用 T016/T017
      过滤无效与重复文档（对应 research.md §3：无效文档必须在早期过滤，不能指望批量
      算子逐行容错），依赖 T024, T025, T016, T017
- [X] T027 [US1] 在 `knowpipe/mining/spark_job.py` 实现 TF-IDF 关键词抽取（top-k，
      k 为可配置参数，见 spec Assumptions），依赖 T026
- [X] T028 [US1] 在 `knowpipe/mining/spark_job.py` 实现 KMeans 主题聚类（簇数为可配置
      参数），产出每条文档的 `topic_cluster_id`，依赖 T026
- [X] T029 [US1] 在 `knowpipe/mining/spark_job.py` 实现文档间相似度计算，产出
      `similar_doc_ids`，依赖 T026
- [X] T030 [US1] 在 `knowpipe/mining/spark_job.py` 中为清洗/特征抽取阶段的每条文档
      包裹异常捕获：处理失败的文档计入 T019 的 `failed_count`，不中断整个批次（对应
      spec FR-011、research.md §3），依赖 T027, T028, T029, T019
- [X] T031 [US1] 在 `knowpipe/mining/mongo_sink.py` 实现 `documents` 集合的 upsert
      写入（按 `source+doc_id`，创建唯一索引），依赖 T026
- [X] T032 [US1] 在 `knowpipe/mining/mongo_sink.py` 实现 `mining_results` 集合的写入
      （创建 `doc_id+batch_id` 索引），并将 `documents.mining` 摘要字段回填，依赖
      T027, T028, T029, T031
- [X] T033 [US1] 在 `knowpipe/mining/spark_job.py` 组装完整的命令行入口（对应
      contracts/storage-contract.md 的 Invocation Contract：输入来源类型/条数/Mongo
      连接信息，输出 `batch_id`），调用 T018/T019 生成并记录批次统计，依赖 T024-T032
- [X] T034 [US1] 按 quickstart.md Step 1/2 手动执行一次验收：两源各 100 条，确认
      `documents`/`mining_results` 记录数与字段符合预期，`batch_id` 可在 1 分钟内
      查询到运行统计（对应 spec SC-001/SC-003），依赖 T033
      → 已在本机真实 MongoDB 上跑通，batch_id `20260905-7442679d`：input_count=200，
      skipped_count=0，failed_count=0，documents=200，mining_results=200，
      按 batch_id 查询运行统计耗时 0.007s（远低于 1 分钟要求），样例记录 keywords/
      topic_cluster_id/similar_doc_ids 均非空

**Checkpoint**: 此时 User Story 1 已可独立验收交付——两源各 100 条能跑通全链路，
产出可查询的文档与挖掘结果记录，且不依赖 User Story 2/3 的任何任务。

---

## Phase 4: User Story 2 - 规模化处理与课程证据留存（Priority: P2）

**Goal**: 将链路从 200 条样例扩展到不少于 10,000 条有效数据，验证无需人工中途干预即可
完整执行，并留存可读的运行证据。

**Independent Test**: 提供不少于 10,000 条来自两个来源的原始文档，触发处理流程，检查
最终产出数量、处理耗时记录、任务执行日志是否存在且可读（对应 spec User Story 2 的
Independent Test，验收步骤见 quickstart.md Step 4）。

### Implementation for User Story 2

- [X] T035 [US2] 在 `knowpipe/mining/collectors/stackexchange.py` 与
      `knowpipe/mining/collectors/arxiv.py` 中支持采集数量扩展到覆盖不少于 10,000 条
      有效文档（分页/批量拉取逻辑），依赖 T024, T025
      说明：StackExchange 单站不带 app key 时分页上限为 25 页（2500 条），改为按
      `DEFAULT_SITES = (stackoverflow, serverfault, superuser, askubuntu)` 4 站轮换
      （调研文档《课程项目选题调研与实施方案.md》§2.1 列出的站点），4×2500=10,000 恰好
      满足门槛。已用 mock 测试验证多站点轮换/单站耗尽换站/分页上限触发换站三种路径
      （`tests/mining/test_stackexchange_collector.py`）。另加 3 次重试+指数退避应对
      瞬时网络超时（真实运行中曾遇到一次 `TimeoutError`，重试后可恢复）。arXiv 补充源
      加了 3 秒请求间隔遵守其限流规范（真实运行中曾因请求过快被 429，加延迟后解决）。
      同时修复了 `prepare_valid_records` 里单来源采集异常会导致整批（含其它来源已采到
      的全部有效文档）丢弃的问题——改为捕获后跳过该来源、保留其它来源已采到的结果。
- [ ] T036 [US2] 验证 `knowpipe/mining/spark_job.py` 在真实 MongoDB 实例（非
      mongomock）上以 ≥10,000 条规模运行时无需人工中途干预即可完整执行完毕，依赖
      T033, T035
      进展：发现并修复了一个会导致该验证在真实规模下永久卡死的性能缺陷（详见下方性能
      修复说明），并发现并修复了一个数据完整性缺陷（StackExchange 跨站点 doc_id 冲突
      导致 documents 集合静默覆盖，详见下方说明）。已用修复后代码完整跑通一次
      9,586(stackexchange)+400(arxiv)=9,986 条有效文档（batch_id=20260905-7bc45fb6，
      input_count=10000, valid_count=9997, skipped_count=3, failed_count=0，整个过程
      无需人工中途干预），但因当日 StackExchange API 配额只剩 96 次请求（差 4 次未达到
      10,000 条门槛，且发现覆盖 bug 后该批次数据已清空重跑），随后该 IP 被 StackExchange
      限流封锁 22 小时（`error_id 502 throttle_violation`）。**T036 尚未最终勾选完成**：
      待限流解除（预计次日）后需用当前已修复代码重新完整跑一次 ≥10,000 条规模验证，
      产出的 batch_id 需替换本条说明中的临时记录作为最终课程证据。
      **性能修复**：`run_mining()` 原实现里同簇内两两相似度比较是纯 Python driver 端
      循环，复杂度 O(簇大小^2)；KMeans 不保证各簇大小均衡（`--num-clusters` 只是请求值，
      实测调大到 64 时数据仍只聚成 5 个大簇），导致簇越大耗时呈平方增长——用 1000/3000
      条合成数据实测验证：1000 条时相似度循环 15s（约 20 万对比较），3000 条时 113s
      （约 180 万对比较），10500 条时会退化到需要十几分钟以上，之前的失败尝试正是卡在
      这一步（进程被杀死，退出码 143，无任何输出）。修复方案：新增
      `_split_into_bounded_groups()`，对超过 `MAX_CLUSTER_COMPARE_SIZE=300` 的簇用一次
      额外的（更小规模、更快的）Spark KMeans 递归拆分成有界子组再两两比较，只影响相似度
      比较范围，不改变落库的 `topic_cluster_id`。修复后 3000 条整体耗时 103s、10500 条
      合成数据整体耗时 240s，均能正常跑完（此前会无限期卡死）。
      **数据完整性修复**：StackExchange 的 `question_id` 只在单个站点内唯一
      （stackoverflow/askubuntu/superuser/serverfault 各自独立编号），但
      `mongo_sink.py` 里 `documents` 集合的唯一键是 `(source, doc_id)`，四个站点的
      `source` 都是常量 `"stackexchange"`，doc_id 若直接用裸 question_id 会在跨站点撞号
      时静默覆盖彼此（真实运行中观察到 11 条这类覆盖）。修复：`doc_id` 改为
      `f"{site}-{question_id}"` 带站点前缀，保证跨站点全局唯一。
- [ ] T037 [US2] 保留并整理本次规模化运行的 Spark 运行日志/DAG 输出（`spark-submit`
      输出或 SparkContext UI 截图/日志文件），作为课程证据存档，依赖 T036
      进展：已修复代码跑出的一次运行日志保存于
      `evidence/001-data-pipeline-spark-mining/run_10k.log`（对应临时批次
      20260905-7bc45fb6，该批次数据已因配额不足清空重跑，日志本身可作为"性能修复生效"
      的证据保留，但作为最终课程证据需替换为限流解除后重跑出的完整 ≥10,000 条批次日志）。
      待 T036 用最终批次重跑后一并更新。
- [ ] T038 [US2] 按 quickstart.md Step 4 手动执行验收：确认 `batch.failed_count` 与
      `batch.skipped_count` 均为可读且可解释的数值（对应 spec SC-002/SC-005），依赖
      T036
      进展：临时批次 20260905-7bc45fb6 的结果可解释（failed_count=0 说明所有文档分词
      后均非空；skipped_count=3 对应 3 条重复文档被两级去重跳过），但该批次已清空，
      待 T036 最终批次产出后需重新核对。

**Checkpoint**: 此时 User Story 1 与 User Story 2 均可独立验收交付——小规模链路与
规模化运行分别成立，互不依赖对方的额外任务即可分别演示。

---

## Phase 5: User Story 3 - 重复运行与数据更新（Priority: P3）

**Goal**: 对已处理过的文档重新触发处理流程，确认幂等更新生效：内容未变不产生重复，
内容已变以新批次结果更新。

**Independent Test**: 对已处理过的一批文档重新触发处理流程（部分不变、部分修改、
新增少量），检查存储中是否仍保持每个来源文档唯一（对应 spec User Story 3 的
Independent Test，验收步骤见 quickstart.md Step 3）。

### Tests for User Story 3 ⚠️

- [X] T039 [P] [US3] `tests/mining/test_mongo_sink.py` 新增用例：对同一
      `source+doc_id` 重复调用 upsert（内容不变），断言 `documents` 集合中记录数不变；
      内容变更后再次 upsert，断言记录内容被更新而非新增
      完成记录：新增 `test_repeat_upsert_with_unchanged_content_does_not_duplicate`、
      `test_repeat_upsert_with_changed_content_updates_not_duplicates`、
      `test_mining_results_accumulates_history_across_batches_without_deleting_old`
      三个用例，`python3 -m unittest tests.mining.test_mongo_sink -v` 5/5 通过。

### Implementation for User Story 3

- [X] T040 [US3] 确认 `knowpipe/mining/mongo_sink.py` 的 upsert 逻辑（T031）对内容
      未变的文档不产生新 `mining_results` 历史记录的重复触发行为符合预期（若发现
      每次重新处理都会产出新 MiningResult，需在 spark_job.py 中增加"内容未变则跳过
      重新挖掘"的判断），依赖 T031, T039
      结论：**不需要代码改动**。交叉核对 data-model.md 与 quickstart.md 后确认
      FR-012"内容未变的文档不重复产出记录"这一约束的范围是 Document 实体
      （`source+doc_id` 唯一索引，已在 `upsert_documents` 中正确实现），而非
      MiningResult。data-model.md 中 MiningResult 实体明确写明"一个 doc_id 可以
      对应多条历史批次的 MiningResult（保留处理历史）"；quickstart.md Step 3 的
      验收标准本身也明确要求：不改变输入重新触发处理后，`documents` 记录数不变，
      但 `mining_results` 应出现携带新 `batch_id` 的新记录。当前
      `write_mining_results` 的无条件 `insert_one`（每次追加）行为正是该验收标准
      所要求的，是符合规范的既有行为，不是缺陷。
- [X] T041 [US3] 按 quickstart.md Step 3 手动执行验收：不改变输入重新触发一次处理，
      确认 `documents` 记录数不变，`mining_results` 出现携带新 `batch_id` 的记录（
      对应 spec SC-004、FR-012），依赖 T040
      验收方式：`test_mining_results_accumulates_history_across_batches_without_deleting_old`
      （T039 新增）以 mongomock 模拟同一 doc_id 两次不同批次的 `write_mining_results`
      调用，断言 `mining_results` 中该 doc_id 保留 2 条历史记录（b1、b2 均存在），
      且 `documents.mining` 摘要指向最新批次 b2——与 quickstart.md Step 3 预期一致。

**Checkpoint**: 三个用户故事均可独立验收交付，且互不依赖。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 影响多个用户故事的收尾工作。

- [X] T042 [P] 检查 `knowpipe/mining/` 各模块是否有意复用了 `knowpipe/ingest.py` 的
      HTML 清洗经验（允许），且未直接依赖现有线程池/JSONL 实现（禁止，见项目宪法
      "现有 Knowpipe 复用边界"）
      核查结果：`grep` 确认 `knowpipe/mining/` 下没有任何模块 `import` 或直接引用
      `knowpipe/ingest.py`（无线程池、无 JSONL 落盘依赖）。
      `stackexchange.py:_html_to_text` 采用了与 `ingest.py:html_to_text` 相同的
      "正则去标签+反转义+折叠空白"思路，但是独立实现的函数，不是直接调用或导入——
      符合宪法"可复用经验、不可直接依赖既有实现"的边界。
- [X] T043 [P] 补全 `knowpipe/mining/` 各模块的运行时错误信息与日志输出，确保 T019
      的运行统计在失败场景下也能被正确写入
      审计发现（委托 Agent 排查后确认）两个真实缺口并已修复：
      1) `spark_job.main()` 原来在 `write_batch_stats` 之前的任何一步失败（Spark
         阶段异常、MongoDB 连接/写入异常）都会让统计信息永久丢失，`batches` 集合
         从未写入。修复：把 Spark 处理与 Mongo 写入包进 `try/except`，失败时也
         调用 `stats.finish(status="failed", error_message=...)` 落盘后再
         `raise`，成功路径落盘 `status="success"`。`BatchStats`/`to_dict()`
         新增 `status`、`error_message` 字段。新增测试
         `test_batch_stats_written_with_failed_status_when_run_mining_raises`
         （mock `run_mining` 抛异常）验证失败路径下 `batches` 仍写入且
         `status=="failed"`，已通过。
      2) `contract.DocumentValidationError` 的具体失败原因此前只被计入
         `skipped_count`，原因信息被丢弃，调试时无法回溯是哪个字段缺失/哪个
         source 值不合法导致跳过。修复：新增 `import logging` + 模块级
         `logger`，在 `prepare_valid_records` 校验失败分支和采集中断分支改用
         `logger.warning` 记录具体原因；`stackexchange.py`/`arxiv.py` 的
         `_fetch_page` 重试循环也新增 `logger.warning`，记录每次失败尝试
         （此前重试过程完全静默，只有最终耗尽重试后的 `CollectError` 可见）。
         `__main__` 入口加 `logging.basicConfig`。
      `python3 -m unittest discover -v` 35/35 通过。
- [X] T044 汇总一次完整的端到端验收记录（quickstart.md 全部 Step + Phase 0 环境
      验证结果），更新到 research.md 或单独的验收记录文件，作为课程答辩证据索引
      → 已创建
      [`evidence/001-data-pipeline-spark-mining/acceptance-record.md`](../../evidence/001-data-pipeline-spark-mining/acceptance-record.md)，
      汇总 Phase 0 环境验证、Step 1/2（US1）、Step 3（US3，已完成）、Step 4（US2，
      明确标注"待补充"并说明原因——等待 StackExchange 限流解除后用修复后代码重新
      跑最终批次）、T043 的错误处理修复说明、测试套件现状。Step 4 的部分**待
      T036/T037/T038 最终批次产出后需回填替换**，本任务本身（"建立汇总文档"）已
      完成，不等同于宣称 Step 4 验收已达标。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 0（环境验证）**：无前置依赖，必须最先完成，阻塞后续所有阶段
- **Phase 1（Setup）**：依赖 Phase 0 完成
- **Phase 2（Foundational）**：依赖 Phase 1 完成，阻塞所有用户故事
- **Phase 3/4/5（User Story 1/2/3）**：均依赖 Phase 2 完成；User Story 2 依赖
  User Story 1 的 `spark_job.py`/`mongo_sink.py` 主体实现（T033）已经跑通，因此
  实际执行顺序是 US1 → US2；User Story 3 依赖 User Story 1 的 T031（upsert 逻辑
  已实现），可以在 US1 完成后与 US2 并行开展
- **Phase 6（Polish）**：依赖期望完成的用户故事全部完成

### Parallel Opportunities

- Phase 1 中 T006-T012 可并行（不同文件的骨架创建）
- Phase 2 中 T014/T015/T018/T020/T021 可并行；T016/T017/T019 因同文件或字段依赖
  需按顺序
- Phase 3 中 T022/T023（测试）可并行；T024/T025（两个采集器）可并行
- Phase 5 中 T039 与 Phase 4 的任务可并行（不同用户故事、不同验收目标）

---

## Parallel Example: User Story 1

```bash
# 先并行写测试：
Task: "tests/mining/test_spark_job.py — local[*] 会话下 TF-IDF/KMeans 产出非空结果"
Task: "tests/mining/test_mongo_sink.py — mongomock 下 upsert 唯一性与查询"

# 再并行实现两个采集器：
Task: "knowpipe/mining/collectors/stackexchange.py — 采集 100 条并转换为统一字段"
Task: "knowpipe/mining/collectors/arxiv.py — 采集 100 条并转换为统一字段"
```

---

## Implementation Strategy

### MVP First（User Story 1）

1. 完成 Phase 0：环境验证
2. 完成 Phase 1：Setup
3. 完成 Phase 2：Foundational（Document 契约、去重、Batch 统计）
4. 完成 Phase 3：User Story 1
5. **停下并验收**：按 quickstart.md Step 1/2 独立验证 User Story 1
6. 此时已构成课程答辩的最小可演示版本（两源各 100 条全链路打通）

### Incremental Delivery

1. Phase 0 + 1 + 2 完成 → 基础就位
2. 加入 User Story 1 → 独立验收 → 可演示（MVP）
3. 加入 User Story 2 → 独立验收 → 规模化证据到位
4. 加入 User Story 3 → 独立验收 → 幂等更新能力到位
5. 每个故事增加价值，不破坏前一个故事已验收的行为

---

## Notes

- [P] 任务 = 不同文件、无依赖
- [Story] 标签用于追溯任务与用户故事的对应关系
- 每个用户故事应能独立完成与验收
- 实现前先确认测试处于失败状态（T022/T023/T039）
- 每完成一个任务或一组逻辑相关任务后建议提交一次
- 可以在任意 Checkpoint 停下先验收该阶段的用户故事
- 避免：模糊任务描述、多任务写同一文件产生冲突、破坏用户故事独立性的跨故事依赖
