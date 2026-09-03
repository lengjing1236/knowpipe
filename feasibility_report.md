# 开发前可行性确认报告

本报告基于 [audit_report.md](/home/lengjing1236/knowpipe/audit_report.md) 的课程约束进行只读核验。所有判断区分为：命令或代码已证明的事实、根据材料作出的推断、必须由人确认的课程决策。

## A. 已验证事实及证据

### 运行环境

| 项目 | 已验证结果 | 证据/边界 |
|---|---|---|
| Java | `/usr/bin/java`，OpenJDK 17.0.20 | `java -version` 成功；`JAVA_HOME` 未设置 |
| Python | `/usr/bin/python3`，Python 3.10.12 | `python3 --version` 成功；`python` 命令不存在 |
| `spark-submit` | 不存在 | `command -v spark-submit` 无输出 |
| PySpark | 不可导入 | `ModuleNotFoundError: No module named 'pyspark'` |
| Spark 运行模式 | 无法验证 | 当前没有 Spark 命令、配置或集群连接信息 |
| MongoDB 服务 | 未发现 | `mongod`、`mongosh`、`mongo` 均不存在；无相关进程和监听端口 |
| 容器环境 | 未发现 | `docker`、`podman`、`nerdctl` 均不存在 |
| Python Web/挖掘包 | Flask 3.1.2、scikit-learn 1.7.2、pandas 2.3.3、numpy 2.2.6 可导入 | 没有 FastAPI、Uvicorn、PyMongo、PySpark |
| 仓库依赖 | 没有 `requirements.txt`、`pyproject.toml`、`setup.py`、Docker 文件或 compose 文件 | 当前项目依赖主要是 Python 标准库；README 也明确如此：[README.md](/home/lengjing1236/knowpipe/README.md:194) |
| 已验证启动方式 | `python3 -m knowpipe --help` 和 `python3 -m knowpipe process --help` 可运行 | [knowpipe/__main__.py](/home/lengjing1236/knowpipe/knowpipe/__main__.py:1)；[knowpipe/cli.py](/home/lengjing1236/knowpipe/knowpipe/cli.py:624) |

结论：本机目前只能直接运行现有 Knowpipe CLI 和普通 Python/Flask 代码，不能直接运行 Spark 或连接本机 MongoDB。Flask 可用不代表仓库已有 Web 应用。

### 本地课程材料与数据

- 未发现课程提供的数据集、数据清单、团队分工表、部署说明或 SDD 模板。
- 现有正式文件主要是：
  - [README.md](/home/lengjing1236/knowpipe/README.md)
  - [DESIGN.md](/home/lengjing1236/knowpipe/DESIGN.md)
  - [audit_report.md](/home/lengjing1236/knowpipe/audit_report.md)
  - 两份课程 PDF
  - `knowpipe/` 和 `tests/`
- `DESIGN.md` 是设计说明，不是完整课程需求规约。
- 本地存在但被 `.gitignore` 忽略的运行产物：
  - `demo/article_personalai.txt`：1 篇文章，约 30 KB；
  - `demo/seed_cards.jsonl`：10 张种子卡；
  - `memory/cards.jsonl`：92 行知识卡；
  - B 站缓存：2 个 transcript 文件。
- 这些不是课程要求的 1 万条原始文档，也没有进入当前 Git HEAD。
- 当前 Git 有 5 次本地提交，但 `git remote -v` 无输出，尚未验证课程要求的远程仓库地址。

### 当前 Knowpipe 的可行挖掘基础

代码已经证明存在：

- 字符 n-gram/token TF-IDF 候选召回：[store.py](/home/lengjing1236/knowpipe/knowpipe/store.py:14)
- `new/known/refine/contradict` 四类知识差分：[brain.py](/home/lengjing1236/knowpipe/knowpipe/brain.py:545)
- URL、文件、B 站、Podcast 输入适配器：[ingest.py](/home/lengjing1236/knowpipe/knowpipe/ingest.py:17)、[podcast.py](/home/lengjing1236/knowpipe/knowpipe/podcast.py:126)
- 16 项本地核心测试全部通过：[tests/test_core.py](/home/lengjing1236/knowpipe/tests/test_core.py:16)

这些只能证明“本地文本处理和轻量检索原型可行”，不能证明 Spark、MongoDB、大规模数据或 Web 链路可行。

## B. 未验证事实

以下事项当前没有本机或仓库证据，必须由你或教师确认：

1. 教师是否提供 Spark 集群、PySpark 环境和 MongoDB 实例。
2. Spark 版本、`spark-submit` 入口、master 地址、executor 资源以及是否允许 local 模式。
3. MongoDB 地址、认证方式、版本、数据库权限和是否允许 MongoDB Spark Connector。
4. 课程最终认可的数据源、许可证、数据规模和多源定义。
5. 是否允许安装依赖；若不允许，Spark/PyMongo/Web 运行环境从哪里提供。
6. 用户角色、个性化定义和推荐评价方式。
7. SDD 模板、分工表、Git 远程地址及提交渠道。

根据课程材料可以推断：文档数据应达到至少 1 万条，并需要完整覆盖数据处理、MongoDB 管理与挖掘、后端服务、Web 交互四层；这仍然是课程约束，不是当前项目已经满足的事实。

## C. 数据方案对照表

下表都是候选方案，不代表当前已获授权或已可下载。

| 候选方案 | 来源与授权/合规 | 1 万条可行性 | 可能字段 | 获取难度 | 是否真正多源 |
|---|---|---|---|---|---|
| Stack Exchange 多站点数据 | Stack Overflow、Server Fault、Super User 等数据转储；内容授权版本、归因和 Share-Alike 条款需核实 | 从数据量推断较容易达到，但尚未取得实际文件和条数证明 | `site`、`post_id`、`title`、`body`、`tags`、`score`、`accepted_answer`、`created_at`、`url` | 中等；离线 dump 解析较重，但避免实时爬取 | 是，同一 schema 下的多个技术社区 |
| GitHub Issues/Discussions/README | 内容许可证按仓库变化，不能把“公开可见”直接视为可自由汇总；API 条款、速率限制和仓库许可需逐仓核实 | 选定足够多的活跃技术仓库后可能达到，但未验证 API 配额和数据量 | `repo`、`language`、`issue_id`、`title`、`body`、`labels`、`comments`、`created_at`、`url` | 中高；API 分页、限流、去重和许可证审查复杂 | 是，多个仓库/语言/项目来源 |
| arXiv + OpenAlex/Crossref 技术论文元数据 | OpenAlex 元数据通常采用开放许可；arXiv 全文和摘要授权存在差异，Crossref/API 条款也需确认 | 元数据/摘要数量推断上很容易达到，但没有当前查询结果证明 | `source`、`paper_id`、`title`、`abstract`、`authors`、`categories`、`published_at`、`doi`、`url` | 中等；接口相对规整，但需处理重复记录和摘要缺失 | 是，但更像“多个学术元数据源” |

当前最适合短周期的候选是 Stack Exchange 多站点数据，因为字段相对统一、技术文本明确，并且可以使用离线转储；但许可证、实际 dump 可得性和教师认可仍是待确认事项。

## D. 挖掘与个性化方案对照表

| 组合 | Spark 参与程度 | 可解释性 | 评价方法 | 工作量 | 答辩风险 |
|---|---|---|---|---|---|
| TF-IDF/关键词 + 用户主题偏好排序 | 高：Spark MLlib `HashingTF/IDF` 或 `CountVectorizer` | 高，可展示关键词权重和打分公式 | Precision@K、Recall@K、NDCG，或人工相关性评分 | 低～中 | 低；需要明确用户偏好如何获得 |
| Spark KMeans 主题聚类 + 用户选择主题推荐 | 很高：TF-IDF 特征、KMeans、主题统计均可在 Spark 中完成 | 中等；聚类结果需要解释和命名 | Silhouette、主题关键词一致性、人工主题相关性 | 中等 | 中；`K`、中文分词和主题可解释性可能被追问 |
| 文档相似度 + 收藏历史画像 | 中高：Spark 生成向量和相似度，用户行为用于画像 | 高；容易解释“喜欢过的文档如何影响推荐” | 留出历史记录做 HitRate@K、Precision@K、NDCG | 中高 | 中高；需要真实用户行为，存在冷启动和数据稀疏问题 |

建议暂不使用 LLM 作为核心挖掘算法。LLM 可以用于摘要或解释，但核心结果应由 Spark 可执行、可度量的特征/聚类/排序算法产生。

## E. 推荐的第一个技术切片

推荐组合：

**Stack Exchange 多站点技术文档候选 + TF-IDF/关键词 + 显式用户主题偏好排序。**

推荐理由：

- 数据 schema 统一，适合多站点合并；
- 文本技术属性明确；
- TF-IDF 适合 Spark MLlib，算法可解释；
- 个性化只需一个主题偏好向量，不需要先收集大量真实行为；
- 评价可以先用人工相关性标注或小规模留出集；
- 比 KMeans 和行为画像更容易在短周期内完成和答辩。

但它依赖以下未确认事实：

- 教师认可 Stack Exchange 数据及其许可证；
- 能取得至少 1 万条原始文档；
- 课程提供可运行的 Spark 环境；
- 课程提供或允许使用 MongoDB；
- 教师接受“显式主题偏好”作为个性化定义。

第一个切片建议只处理 100 条已确认样本文档：

1. 输入字段：`doc_id`、`site`、`title`、`body`、`tags`、`source_url`。
2. Spark 读取样本并执行文本清洗、TF-IDF 和 top-k 关键词提取。
3. 使用一个明确的用户主题偏好，例如用户选择 `Python`、`Spark`、`MongoDB` 三个主题。
4. 计算“主题偏好匹配分 + 文档关键词分”的排序结果。
5. 将原始文档和挖掘结果写入 MongoDB。
6. 用最小只读 API 返回 top-k 文档。
7. 用一个静态页面展示关键词和推荐列表。

切片验收证据：

- Spark 运行命令、master/运行模式、输入输出条数和耗时；
- MongoDB 集合、索引和查询结果；
- API JSON 输出；
- 浏览器页面截图；
- 关键词或推荐排序的评价结果；
- 对应 Git 提交和规约记录。

当前本机没有 Spark 和 MongoDB，因此该切片现在只能完成“方案设计”，不能声称已经可运行。

## F. 需要你或教师回答的问题

1. 教师最终批准哪个数据源？是否有现成数据集、许可证说明和至少 1 万条规模证明？
2. Spark 的版本、`spark-submit` 命令、运行模式和集群地址是什么？本机是否允许安装 PySpark，还是必须使用课程环境？
3. MongoDB 实例在哪里？连接地址、账号权限、版本以及是否允许 Spark Connector 是什么？
4. 个性化是否采用“用户显式选择主题偏好”？用户角色是否只设为单一学习者，评价指标采用什么？
5. Web、SDD、Git 和交付环境有哪些硬性限制？是否提供模板、远程仓库和部署机器？
