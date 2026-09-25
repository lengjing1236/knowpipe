# Phase 0 Research: 数据管道与 Spark 挖掘

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档解决 Technical Context 中未被调研文档直接固定的技术细节。凡调研文档已经决定
的事项（Spark/MongoDB 选型本身、数据源选择、字段结构）不在此重复论证。

## 1. 本机 PySpark / MongoDB 环境验证 [已验证]

**现状（更新于 2026-09-05 Phase 0 spike）**：本机已完成以下验证，结论：环境可用，
不需要走调研文档第 4 节的降级路径。

- **T001 PySpark 可导入** [已验证]：`pip3 install --user pyspark`（需先
  `unset http_proxy https_proxy`，本机默认代理只支持 HTTP，会导致 HTTPS 请求失败）
  成功安装 `pyspark==4.2.0`（含 `py4j 0.10.9.9`）。`python3 -c "import pyspark"`
  正常返回版本号。
- **T002 local[*] SparkSession 可用** [已验证]：以 Java 17.0.20（此前已验证）为
  运行时，`SparkSession.builder.master('local[*]').getOrCreate()` 成功创建会话，
  对一个 3 行 DataFrame 执行 `.count()` 返回正确结果 3，无需教师审批的备用路径。
- **T003 PyMongo 可连接真实 MongoDB 实例** [已验证]：本机没有系统级 `mongod`
  服务，也没有 Docker/Podman，因此改为在用户目录下直接部署官方发布的 MongoDB
  Community 二进制包（`mongodb-linux-x86_64-ubuntu2204-7.0.14.tgz`，从
  `fastdl.mongodb.org` 下载，无需 root/sudo），以
  `mongod --dbpath ~/mongodb-local/data --port 27017 --bind_ip 127.0.0.1 --fork`
  方式启动为本机真实实例（非 mongomock）。`pymongo.MongoClient(...).admin.command('ping')`
  返回 `{'ok': 1.0}`，`server_info()['version']` 确认为 `7.0.14`。这不是 SQLite
  顶替，是同一个 MongoDB 发行版的真实进程，符合 constitution Principle III 的
  "不可替代边界"要求。

**Decision**: 环境验证 spike 已完成且全部通过，无需触发降级路径。后续 Phase 1+
任务可以直接对接本机 `mongodb://127.0.0.1:27017` 这个真实 MongoDB 实例，以及本机
`local[*]` Spark 环境。

**Rationale**: 这是当前唯一的真正未知项——不是设计选择，是"能不能跑起来"的事实
问题，必须尽早暴露。验证结果表明本机环境本身没有阻塞性问题，此前的顾虑（未验证
PySpark/MongoDB）已经解除。

**Alternatives considered**: 跳过验证直接写业务代码，遇到环境问题再排查——被拒绝，
因为课程周期短，环境问题应尽早暴露而非留到集成阶段。用 Docker 部署 MongoDB——本机
未安装 Docker/Podman，改用官方二进制包直接以用户权限运行，效果等价且不需要额外
安装系统级组件。

## 2. 去重判定规则

**Decision**: 采用两级判定：
1. 精确去重：`source + source_url` 完全相同 → 判定为同一文档，后到的记录以
   `source+doc_id` 做 upsert 覆盖（对应 spec FR-012 的幂等更新）。
2. 近似去重（跨来源，如同一篇论文的预印本与摘要）：对清洗后正文计算内容哈希
   （如标题+正文规范化后取 SHA-256），哈希相同判定为重复，仅保留先处理的一条，
   重复的一条记录为"跳过"并计入运行统计（spec FR-010 的失败/跳过条数）。

**Rationale**: 精确去重解决"同一条数据被重复采集/重新处理"的常见情况（对应 spec
User Story 3）；内容哈希解决"两个不同来源但内容雷同"的边界情况（spec Edge Cases
第二条），且判定依据是可复现的计算结果，符合 FR-003 对"不依赖人工判断"的要求。

**Alternatives considered**: 用文本相似度阈值判定近似重复——被拒绝，因为相似度
阈值需要调参且不是二元判定，容易在小数据量下产生不可解释的边界结果；内容哈希更
简单、确定、可解释，适合课程证据展示。命题级/语义级去重（如向量相似度）留给未来
若发现哈希漏检过多再引入，不在本 feature 首个实现范围内。

## 3. 单条文档失败的隔离方式

**Decision**: Spark 作业内对每条文档的清洗/特征抽取逐条包裹异常捕获，失败记录写入
一个独立的"失败清单"（可以是运行统计的一部分），不中断整个 DataFrame 的批处理；
Spark 原生的 DataFrame 算子（如 `TF-IDF`、`KMeans`）本身是整列批量操作，因此"跳过
无效文档"必须在这些算子执行前的清洗/校验阶段完成（即无效文档在早期被过滤出主
DataFrame，不会进入 TF-IDF/KMeans 阶段），而不是指望批量算子内部做逐行异常处理。

**Rationale**: 对应 spec FR-011。Spark 的 DataFrame API 不支持"某一行计算异常、
其余行继续"的细粒度容错语义，因此必须在进入核心算子之前就把无效/异常文档过滤掉，
这是 Spark 编程模型的自然约束，不是额外设计。

**Alternatives considered**: 让整个批次失败后重跑——被拒绝，因为 spec 明确要求
单条失败不影响批次整体产出。

## 4. 测试策略：真实 MongoDB vs mongomock

**Decision**: 单元测试（校验 upsert 逻辑、索引声明、字段契约）使用 `mongomock`，
不依赖真实数据库实例，可在任意开发环境快速运行；User Story 1（两源各 100 条）与
User Story 2（≥10,000 条）的验收必须对接真实 MongoDB 实例，作为课程证据的一部分，
不能用 `mongomock` 的结果代替真实运行证据。

**Rationale**: 项目宪法 Principle III 明确"JSONL/SQLite 不能算 MongoDB"，这里
进一步明确 `mongomock` 同理只能用于开发期单元测试，不能替代验收阶段的真实实例
运行证据，避免验收证据造假的边界被误解。

**Alternatives considered**: 全程用真实 MongoDB 做所有测试——被拒绝，因为会让
单元测试依赖外部服务可用性，拖慢日常开发迭代；纯 mongomock 不做真实实例验证——
被拒绝，因为违反宪法对课程证据真实性的要求。

## Resolved Technical Context

以上四项已覆盖 Technical Context 中所有需要决策而调研文档未直接给出的细节，
无遗留 NEEDS CLARIFICATION 项。
