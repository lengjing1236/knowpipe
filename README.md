# Knowpipe

"大数据综合实践"课程项目：基于 Spark 与 MongoDB 的多源技术内容知识挖掘与个性化
发现 Web 系统，对应
[specs/001-data-pipeline-spark-mining](specs/001-data-pipeline-spark-mining) 与
[specs/002-personalized-web](specs/002-personalized-web) 两个 spec-kit feature。

## 目录

- [架构](#架构)
- [环境准备](#环境准备)
- [运行数据挖掘作业](#运行数据挖掘作业feature-001)
- [运行个性化 Web](#运行个性化-webfeature-002)
- [测试](#测试)
- [课程过程文档](#课程过程文档)

---

从 Stack Exchange（主源）与 arXiv CS（补充源）采集技术内容，用 Spark 完成清洗、
去重、TF-IDF 关键词抽取、KMeans 主题聚类与相似度计算，写入 MongoDB；再基于这些
结果，为登录用户判定每个知识单元的 `known / refine / new / possible_conflict`
状态，并通过 Flask API 与单页 Web 展示个性化推荐。完整需求、数据模型与验收记录见
[课程过程文档](#课程过程文档)。

### 架构

```
Stack Exchange API ─┐
arXiv API           ─┤→ [采集/校验/去重] → [Spark: TF-IDF/KMeans/相似度] → MongoDB
                                                                    (documents,
                                                               mining_results, batches)
                                                                          │
                                                        ┌─────────────────┘
                                                        ▼
                                  [Spark: 推荐分数] → user_knowledge ← [classify 判定]
                                                                          │
                                                                          ▼
                                                Flask API (/api/*) → 单页 Web
```

- `knowpipe/mining/`：采集器（`collectors/stackexchange.py`、`collectors/arxiv.py`）、
  字段契约（`contract.py`）、两级去重（`dedup.py`）、批次统计（`batch.py`）、Spark
  挖掘作业入口（`spark_job.py`）、MongoDB 写入层（`mongo_sink.py`）。
- `knowpipe/web/`：登录鉴权（`auth.py`）、知识状态判定（`classify.py`）、非个性化
  基线排序（`baseline.py`）、推荐分数 Spark 作业（`score_job.py`）、API 路由
  （`routes_api.py`）、页面路由与模板（`routes_pages.py`、`templates/`）、Flask
  应用工厂（`app.py`）。

### 环境准备

```bash
pip install -r requirements.txt   # pyspark, pymongo, mongomock, flask

# 需要 Java 17+（PySpark 运行时）
java -version

# 需要一个可连接的 MongoDB 实例（本机或课程提供的实例，不可用 mongomock 代替）
python3 -c "import pymongo; pymongo.MongoClient('mongodb://localhost:27017').admin.command('ping')"
```

### 运行数据挖掘作业（Feature 001）

```bash
python3 -m knowpipe.mining.spark_job \
  --sources stackexchange arxiv \
  --target-count 100 \
  --source-target-count stackexchange=10000 arxiv=500 \
  --mongo-uri "mongodb://localhost:27017/knowpipe_mining"
```

命令输出本次运行的 `batch_id`；产出写入 `documents`（原始文档）、`mining_results`
（关键词/主题簇/相似文档）与 `batches`（运行统计，可按 `batch_id` 追溯起止时间与
输入/有效/跳过/失败计数）三个集合。`arXiv` 为补充源，不计入课程要求的万级规模
门槛（`--source-target-count` 可分别覆盖各来源目标数）。完整验证步骤见
[Feature 001 quickstart](specs/001-data-pipeline-spark-mining/quickstart.md)。

### 运行个性化 Web（Feature 002）

依赖 Feature 001 已产出的 `documents`/`mining_results`。

```bash
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="knowpipe_mining"
export SECRET_KEY="<生产环境请设置为随机值，默认仅供本地开发>"

python3 -m knowpipe.web.app       # 默认监听 127.0.0.1:5000，debug 模式仅供本地开发
```

打开 `http://127.0.0.1:5000/login` 注册/登录，登录后跳转到单页 Web，可设置已知
主题、查看新知识/深化知识推荐及其判定依据。API 契约见
[api-contract.md](specs/002-personalized-web/contracts/api-contract.md)，
完整验证步骤（含 curl 示例）见
[Feature 002 quickstart](specs/002-personalized-web/quickstart.md)。

推荐排序分数由独立的 Spark 批处理作业计算（不在请求路径中同步计算）：

```bash
python3 -m knowpipe.web.score_job --mongo-uri "mongodb://localhost:27017" --mongo-db knowpipe_mining
```

> **安全提示**：`create_app()` 默认的 `SECRET_KEY`/`app.run(debug=True)` 仅适合本地
> 开发和课程演示；`/api/*` 之外的接口未做速率限制，若部署到公网需自行加固。

### 测试

```bash
python3 -m unittest discover -v
```

`tests/mining/`、`tests/web/` 下的单元测试默认使用 `mongomock`，可离线快速运行；
`tests/web/test_score_job.py` 使用真实 `local[*]` SparkSession（非 mock）。规模化
与端到端验收必须对接真实 MongoDB 实例，结果记录在 [evidence/](evidence/)。

### 课程过程文档

| Feature | 规格 | 数据模型/契约 | Quickstart | 验收记录 |
|---|---|---|---|---|
| 001 数据管道与 Spark 挖掘 | [spec.md](specs/001-data-pipeline-spark-mining/spec.md) | [data-model.md](specs/001-data-pipeline-spark-mining/data-model.md) / [storage-contract.md](specs/001-data-pipeline-spark-mining/contracts/storage-contract.md) | [quickstart.md](specs/001-data-pipeline-spark-mining/quickstart.md) | [acceptance-record.md](evidence/001-data-pipeline-spark-mining/acceptance-record.md) |
| 002 个性化知识分类与 Web 展示 | [spec.md](specs/002-personalized-web/spec.md) | [data-model.md](specs/002-personalized-web/data-model.md) / [api-contract.md](specs/002-personalized-web/contracts/api-contract.md) | [quickstart.md](specs/002-personalized-web/quickstart.md) | [acceptance-record.md](evidence/002-personalized-web/acceptance-record.md) |

课程答辩材料见 [defense/](defense/)（项目报告、答辩讲稿、生成的 PPT）。
