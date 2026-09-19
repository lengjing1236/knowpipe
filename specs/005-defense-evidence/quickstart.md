# 答辩与人工评价

[方案] 为至少两个不同用户画像，分别获取个性化与 baseline 前 K 篇文档，去掉同一文档重复项。文档统一以 source:doc_id 标识；不得拿关键词 ID 与文档 ID 直接比较。

请项目成员把两种方法的候选文档合并、隐藏算法来源后人工标注：0 无关、1 略相关、2 相关、3 高度相关。JSON 文件是数组，每个查询包含 query_id、personalized（文档 ID 数组）、baseline（文档 ID 数组）、judgments（文档 ID 到 0–3 的映射）。所有前 K 候选必须有人标注。

```bash
python3 -m knowpipe.evaluation.metrics judgments.json --k 10 > evaluation-results.json
```

保存排序批次、用户画像（匿名）、标注日期、标注说明与输出。工具不会生成标签；差值为负也是有效结果，需要解释原因，不能改标签凑提升。

Precision@K：前 K 中相关条数 / K；短列表仍除 K。NDCG@K：按 2^grade-1 增益和 log2(rank+1) 折损，除以该查询人工候选池的理想值。候选池不等于全库；两用户小样本不代表统计显著性。

## 最终门槛

- [ ] 最新文献批次：有效 Stack Exchange ≥10,000，arXiv 为补充；保留失败/去重统计与日志。
- [ ] 至少两种画像真实人工评价完成，保存指标与对照基线。
- [ ] 真实 RSS 的节目 → 文字稿 → Spark → Mongo → 通知走通。
- [ ] 外部网络访问 HTTPS，两个账户隔离，重启数据不丢失。
- [ ] 能解释 Spark 分区、TF-IDF、KMeans、相似度、Mongo 索引与增量任务；掌握取舍后再更新演讲稿/PPT。

当前 local[2] 是单 JVM 本地执行模式。容器部署在公网不会自动变成多机 Spark 集群。若选用独立集群，设置 SPARK_MASTER=spark://...，确保驱动与 executors 网络互通、同版本 Python/依赖及 knowpipe 包可用，再保存真实 executor/DAG 证据。
