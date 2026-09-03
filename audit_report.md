# 只读审计报告

审计范围：`README.md`、`DESIGN.md`、两份课程 PDF、`knowpipe/`、`tests/`、当前 Git 历史。  
审计动作未修改文件、未安装依赖、未写实现代码。已执行 `python3 -m unittest discover -v`，16 项测试全部通过；这些测试主要使用 mock 和临时目录，未验证真实 Spark、MongoDB、Web 或外部数据集。

## 1. 当前仓库已经实现的核心价值

Knowpipe 已经实现了一个可运行的本地知识精炼 CLI：把 URL、文本、文件、B 站字幕/逐字稿或 Podcast transcript 切块，通过 TF-IDF 候选召回和可选 LLM/启发式分类识别新知识，持久化为 JSONL/SQLite 知识卡，并生成 Markdown 报告和记忆库问答。

证据：

- [README.md](/home/lengjing1236/knowpipe/README.md:3)
- [knowpipe/cli.py](/home/lengjing1236/knowpipe/knowpipe/cli.py:50)
- [knowpipe/store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:48)
- [knowpipe/brain.py](/home/lengjing1236/knowpipe/knowpipe/brain.py:157)

这是一条“个人知识加工管道”，还不是课程要求的 Spark + MongoDB + Web 大数据系统。

## 2. 可直接复用的模块、输入输出与证据

| 模块 | 可复用能力 | 输入 → 输出 | 证据 |
|---|---|---|---|
| `ingest` | URL/文本/本地文件读取、HTML 清洗、分块 | URL、字符串、文件 → 清洗文本、`list[str]` 分块 | [ingest.py](/home/lengjing1236/knowpipe/knowpipe/ingest.py:17)；[ingest.py](/home/lengjing1236/knowpipe/knowpipe/ingest.py:65) |
| `bilibili` | BV 号解析、官方字幕、缓存、可选 Lark/Whisper 转写 | BV + P → `{title,text,method,...}` | [bilibili.py](/home/lengjing1236/knowpipe/knowpipe/bilibili.py:124)；[bilibili.py](/home/lengjing1236/knowpipe/knowpipe/bilibili.py:425) |
| `podcast` | RSS/Atom 解析、transcript 清洗、音频/Whisper 兜底、缓存 | Feed URL/episode → episode 元数据和文本 | [podcast.py](/home/lengjing1236/knowpipe/knowpipe/podcast.py:126)；[podcast.py](/home/lengjing1236/knowpipe/knowpipe/podcast.py:287) |
| `Brain` | OpenAI 兼容 LLM、manual、heuristic 三种 provider；知识卡分解、四类差分、摘要、问答 | 文本/候选卡 → 卡片、`new/known/refine/contradict` 判定、摘要或答案 | [brain.py](/home/lengjing1236/knowpipe/knowpipe/brain.py:189)；[brain.py](/home/lengjing1236/knowpipe/knowpipe/brain.py:545) |
| `Index` | 字符 n-gram + token TF-IDF 余弦相似度候选召回 | 文本 → top-k 候选及相似度 | [store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:14)；[store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:80) |
| `MemoryStore` | 知识卡 schema、幂等写入、状态、溯源、JSONL 持久化 | claim/source/topic → 知识卡 | [store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:108)；[store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:206) |
| `SQLiteMemoryStore` | SQLite 卡片库、事务保存、状态/主题索引、JSONL 迁移 | JSONL/卡片 → SQLite | [store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:269)；[store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:391) |
| CLI pipeline | 分块 → 抽卡 → 候选召回 → 分类 → 写库 → 报告 | 文本和 Brain → `res` 结果字典、Markdown 报告 | [cli.py](/home/lengjing1236/knowpipe/knowpipe/cli.py:50) |
| `report` | 新知识、深化、冲突、跳过、摘要和问答卡建议 | `res` → Markdown | [report.py](/home/lengjing1236/knowpipe/knowpipe/report.py:11) |
| `watch` / `WatchStore` | Podcast 定时轮询、GUID 去重、失败重试、原子归档、Webhook/SMTP | Feed 列表 → 状态库、归档报告、通知 | [watch.py](/home/lengjing1236/knowpipe/knowpipe/watch.py:120)；[watch_store.py](/home/lengjing1236/knowpipe/knowpipe/watch_store.py:24) |
| 测试 | 16 项核心行为测试全部通过 | 测试场景 → 可重复的本地行为证据 | [test_core.py](/home/lengjing1236/knowpipe/tests/test_core.py:16) |

本地还存在 `demo/`、`memory/`、`cache/` 等运行产物，但它们被 `.gitignore` 忽略，未进入当前 HEAD。比如本地 `memory/cards.jsonl` 只有 92 行，`demo/seed_cards.jsonl` 只有 10 张种子卡，不能作为“至少 1 万条文档”的课程数据证据。

## 3. 课程要求对照表

课程依据：

- 课程说明 PDF 第 3 页：文档类非结构化数据与 MongoDB。
- 第 4 页：文档数据最低 1 万条，数据来源和合规要求。
- 第 8 页：不得做纯增删改查，必须覆盖存储管理、数据挖掘、Web 业务应用。
- 第 10～11 页：交互层、业务层、数据管理与挖掘层、大数据计算处理层四层链路。
- 第 12、14～16 页：SDD、提示词记录、Git、报告、演示视频、评分维度和答辩要求。
- 参考资料 PDF 第 3～6 页：Spark、MongoDB、Spark MLlib、Web 框架和公开数据集平台。

| 课程要求 | 当前状态 | 缺口 | 最小补齐方案 |
|---|---|---|---|
| 面向文档类非结构化数据 | 部分满足。能处理文章、字幕、transcript | 没有统一的原始文档集合、schema、文档级数据目录 | 确认一个合法文档数据源，统一为 `doc_id/text/source/time` 等字段 |
| 文档规模不少于 1 万条 | 未证实 | 仓库没有 1 万条数据、统计脚本或规模运行日志 | 获取或获教师批准的数据集；提交数据清单、条数统计、采集时间和出处 |
| 数据采集/底层大数据处理 | 只有 URL、B 站、Podcast 输入适配器 | 没有批量采集任务、HDFS、Spark、Flink 或 Hadoop 运行证据 | 增加一个可复现的批处理入口，并保存 `spark-submit`、输入/输出计数和运行日志 |
| 必须使用 Spark 等大数据处理技术 | 未满足 | 没有 `pyspark`、`spark-submit` 或 Spark 配置 | 使用实际可用的 Spark 环境完成清洗、特征提取或聚类；明确记录 local/standalone/YARN 模式 |
| MongoDB 文档管理 | 未满足 | 当前是 JSONL 和 SQLite；没有 MongoDB 驱动、连接、集合或查询证据 | 使用 MongoDB 保存原始文档、处理结果和挖掘结果；提供集合结构、索引和查询截图/日志 |
| 真实数据挖掘 | 部分满足 | TF-IDF 候选召回和四分类规则是真实算法原型，但主要针对个人知识卡；OpenAI 路径大量依赖 LLM；没有大规模实验、指标和基线 | 选择一个可解释任务，例如 TF-IDF/关键词、主题聚类、情感分类或推荐；在 1 万条文档上给出指标和对比 |
| 后端业务服务 | 未满足 | 没有 HTTP 服务、路由、JSON API 或业务接口 | 增加一个最小 Web API，例如搜索/发现/推荐接口 |
| Web 交互与可视化 | 未满足 | 输出是 CLI、Markdown、Webhook/SMTP；没有前端页面、图表或可视化组件 | 增加一个静态页面和一个图表，调用后端 API 展示关键词、主题分布或推荐结果 |
| 完整四层链路 | 未满足 | 目前只有输入、处理、个人存储和报告；缺 Spark、Mongo、Web 三个课程关键证据 | 做一条最小垂直链路：数据 → Spark → MongoDB → API → 页面 |
| 个性化发现 | 仅有单用户“已知/未知知识”概念 | 没有用户实体、用户角色、兴趣画像、反馈或推荐评价 | 先确认采用显式主题偏好、点击历史或知识库差分中的一种定义，再实现一个可解释的 top-k 发现 |
| 需求规约与设计规约 | 有 `DESIGN.md`，但不是完整 SDD 交付包 | 没有独立需求规约，也没有与最终实现绑定的课程项目规约 | 补齐需求规约、设计规约、接口/数据字典，并保持与实际实现一致 |
| 全部 AI 提示词记录 | 提示词硬编码在 `brain.py`，manual 文件只保存部分判定 | 没有按运行保存的完整 prompt、模型、输入摘要、输出和版本 | 建立 Markdown/JSONL 提示词日志，记录时间、用途、模型、版本和结果摘要 |
| 可追溯 Git 历史 | 本地有 5 次提交 | `git remote -v` 没有输出；demo、数据和 PDF 等重要材料不在 HEAD | 建立可提交的远程仓库或课程指定地址；把规约、数据说明、运行日志和必要 demo 纳入版本管理 |
| 可运行、可演示、可答辩 | CLI 本地测试可运行 | 没有课程目标环境下的整链路演示，也没有 Web/Spark/Mongo 备份视频 | 先完成小样本垂直切片，再做 1 万条规模测试和 5 分钟备份演示 |

## 4. 不能夸大的现有能力

### 不能直接算 Spark

- `ThreadPoolExecutor` 只是 Python 进程内并发，不是 Spark 分布式计算。
- `chunk_text`、TF-IDF、SQLite WAL 都不能证明使用了 Spark Core、Spark SQL 或 Spark MLlib。
- 当前没有 `pyspark` 导入、`spark-submit` 脚本、集群配置、executor 日志或 Spark 输出。

### 不能直接算 MongoDB

- JSONL/SQLite 是本地文件数据库，不是 MongoDB。
- 当前没有 `pymongo`、Mongo URI、collection、BSON、MongoDB 索引或查询证据。
- SQLite 的“文档卡片”不能替代课程要求的 MongoDB 文档数据管理。

### 不能直接算 Web 系统

- CLI、Markdown 报告和内部 Python 函数不是 Web 后端服务。
- Webhook/SMTP 是出站通知，不是用户交互页面。
- 当前没有 Flask/FastAPI/Django/Spring Boot、HTTP 路由、前端资源、ECharts/D3/Vue/React 或浏览器交互。

### 不能直接算完整的数据挖掘成果

- TF-IDF 候选召回是可复用的轻量 NLP/检索算法，具备一定挖掘基础。
- `new/known/refine/contradict` 的判定在 OpenAI 模式下主要由 LLM 完成，不能把 LLM 输出本身等同于自主数据挖掘算法。
- `article` 模式主要是摘要/知识整理，不是分类、聚类、推荐或情感挖掘。
- 目前没有 1 万条文档上的运行结果、训练/推理指标、基线对比、人工标注集或可复现实验报告。

### 不能直接算“多源大数据系统”或“个性化推荐系统”

- 代码中有多种输入适配器，但没有证据证明已稳定采集并统一处理多个真实来源的大规模数据。
- “个人记忆库”是单用户知识状态，不等于多用户画像、推荐策略或个性化发现评价。

## 5. 两种可行路线

### 路线 A：在 Knowpipe 基础上演进

工作量：约 8～12 个有效人日，另加环境和数据获取时间。

主要工作：

1. 把当前输入产物统一成原始文档记录；
2. 增加 Spark 批处理和特征/挖掘任务；
3. 增加 MongoDB 存储适配器；
4. 增加最小 Web API 和页面；
5. 增加用户偏好/历史与推荐定义；
6. 增加规模测试、指标、规约和提示词日志。

风险：

- 当前代码围绕“个人知识卡 CLI”设计，不是文档仓库或 Web 服务；
- 需要同时处理 Spark、MongoDB、Web、原有 LLM/缓存/转写分支；
- 容易做出“原有 CLI + 新增几个接口”的拼接系统，答辩时四层边界不清；
- 现有 demo 和运行数据未纳入 Git，复现性不足。

可答辩性：能够解释已有知识差分、TF-IDF 和反馈闭环，但必须额外证明 Spark、MongoDB、Web 的真实运行，否则核心课程评分项仍然缺失。整体为中等偏低。

### 路线 B：重新搭建一个更小的课程系统

工作量：约 5～8 个有效人日，前提是 Spark、MongoDB、Web 环境和数据源已经可用。

最小形态：

1. 一个确认合法的文档数据集；
2. 一个 Spark 批处理任务：清洗、分词、TF-IDF/关键词或主题聚类；
3. MongoDB 保存原始文档和挖掘结果；
4. 一个最小 Python Web API；
5. 一个 HTML + 图表页面；
6. 一个简单、可解释的个性化规则，例如用户选择主题后返回相似文档 top-k。

风险：

- 需要重新写少量代码；
- 数据获取、Spark 集群和 MongoDB 权限仍可能成为阻塞点；
- 若环境未确认，重新搭建也无法绕过基础设施问题。

可答辩性：四层结构直接对应课程评分点，每个组件职责清楚，容易展示“数据从哪里来、Spark 做了什么、Mongo 存了什么、API 返回什么、页面如何呈现”。整体为较高。

## 6. 推荐路线与第一个可验证技术切片

推荐路线 B。原因是当前 Knowpipe 的核心价值可以作为算法和产品思路参考，但它与课程要求的 Spark + MongoDB + Web 结构差异较大。短周期内，新建一个小而垂直的课程系统更容易形成清晰证据，也不容易把 CLI、LLM 摘要能力误包装成课程要求。

第一个技术切片应只验证一条端到端链路，不先设计复杂架构：

**输入**：从最终确认的数据源取 100 条样本文档，字段至少包括 `doc_id`、`text`、`source`；这 100 条只能作为切片样本，最终仍需扩展到不少于 1 万条。

**处理**：

1. 用实际可运行的 Spark 环境读取样本；
2. 完成文本清洗和一个可解释的挖掘任务，例如 TF-IDF top-k 关键词或主题聚类；
3. 输出处理条数、非空结果数、Spark master/运行模式和耗时；
4. 把文档及挖掘结果写入 MongoDB。

**服务与展示**：

1. 提供一个只读 API，例如 `/api/search?q=...` 或 `/api/discover?topic=...`；
2. 返回文档 ID、来源、关键词/主题、相似度或推荐分数；
3. 一个静态页面用柱状图或列表展示结果；
4. 若采用个性化语义，明确写出用户偏好如何进入打分公式。

**切片验收证据**：

- Spark 运行命令和日志；
- 输入/输出条数统计；
- MongoDB 集合、索引和查询结果；
- API 的 `curl` 输出；
- 浏览器截图或录屏；
- 一个 Git 提交，关联本次规约和运行记录。

切片通过后再做：

- 扩展至至少 1 万条；
- 评估关键词/聚类/推荐质量；
- 增加多源数据；
- 完成需求规约、设计规约、提示词日志、报告、PPT 和备份视频。

如果最终确认没有可用 Spark 集群，也不能把 Python 本地处理或并发代码称为“真实分布式 Spark”；应先向教师确认是否允许 Spark local 模式或申请课程提供的环境。

## 7. 开始编码前仍需确认的事实

尤其需要确认以下事项，当前仓库和课程 PDF 都没有替你确认：

1. **数据来源**：教师数据集、公开数据集还是自行爬取？具体 URL、许可证、robots 要求和获取方式是什么？
2. **数据规模**：最终文档条数是否能达到至少 1 万条？平均长度、语言、重复率和字段结构如何？
3. **多源定义**：至少需要几个来源？是多个站点、多个 RSS，还是不同文档格式？
4. **数据合规**：是否包含个人信息？是否需要脱敏？自行采集是否需要向教师备案？
5. **Spark 条件**：Spark 版本、PySpark 是否已安装、`spark-submit` 是否可用、local/standalone/YARN 哪种模式、executor 资源和运行时长限制。
6. **MongoDB 条件**：MongoDB 版本、连接地址、账号权限、数据库/集合命名、是否允许使用 MongoDB Spark Connector。
7. **Web 条件**：允许使用 Flask、FastAPI 还是其他框架？部署机器、端口、浏览器和前端图表库是否有限制？
8. **用户角色**：单用户学习者，还是需要管理员/普通用户？是否需要登录和 RBAC？
9. **个性化定义**：基于显式主题偏好、点击/收藏历史、用户已知知识，还是用户画像？冷启动怎么处理？
10. **挖掘任务**：关键词、主题聚类、情感分析、相似文档推荐，还是其他任务？是否有标签或人工评测集？
11. **评价指标**：准备使用 precision@k、recall@k、silhouette、主题一致性、人工评分或其他指标？
12. **LLM 使用边界**：是否允许发送数据到云端？LLM 是辅助清洗/解释，还是仅作为可选功能？如何记录每次提示词和模型版本？
13. **团队与交付**：组员、分工、SDD 模板、Git 远程仓库地址、报告/PPT/视频提交渠道和截止时间。
14. **演示环境**：答辩当天是否能访问 Spark/MongoDB？是否需要完全离线运行和备份视频？

结论：当前 Knowpipe 值得保留的是“知识卡分解、TF-IDF 候选召回、差分判定、反馈闭环和输入适配器”这些产品与算法素材；但在课程审计口径下，Spark、MongoDB、Web、1 万条规模和可验证的大数据挖掘成果目前均尚未成立。
