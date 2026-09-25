# Feature 004 实施计划

[方案] 新增 knowpipe/podcasts：network（受限抓取）、feeds（RSS/文字稿解析）、store（状态/索引/订阅）、analysis（Spark）、worker（租约/轮询）。Web 只读处理结果、维护订阅并提供 SSE；不在请求中启动 Spark。

[已验证] 2026-09-16 答辩补充：用户无服务器/域名，目标为编程播客。选择 JS Party 历史真实节目，下载边界调整为 4 MiB 以容纳 2.27 MB RSS；HTML 文字稿仅解析声明地址的段落和发言者。已有节目仍检查延迟文字稿更新，未知且早于已知最新发布时间的节目不自动回填。免费隧道通过持久通知轮询兼容。核心分析仍使用真实 Spark、真实 MongoDB 和 Web；未改变课程规模/人工评价门槛。

采用现有 MongoDB 作为持久任务状态与通知存储。单个 worker 处理有界增量批次；此处不引入 Kafka/Flink。Spark 负责核心统计，ASR 仅是文本输入适配，绝不冒充挖掘算法。

簇标识使用 batch_id + cluster_id，播客结果单独存储，不污染 Feature 001/002 的既有模型语义。首版 Spark 相似度使用分布式 DataFrame 运算；数量上限保护小服务器。

宪法核对：[方案] 保留万条 Stack Exchange 基线；播客不替代课程主源规模；既有 local 模式只代表本机执行，不称为多机集群；公网 Web 与多机 Spark 是独立验收项。
