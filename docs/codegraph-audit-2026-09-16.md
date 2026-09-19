# codegraph 项目分析与答辩差距

[已验证] 2026-09-16 使用 codegraph index_directory 索引 knowpipe/ 共 20 文件；module summary 返回 86 函数、5 类。调用/依赖上下文检查覆盖 run_mining 与 create_app，并以源文件复核。Python import 统计出现 0 与 dependency graph 不一致，故不据此声称“无依赖”。memory_context / memory_search 返回 Memory manager not initialized，未获得历史记忆。

| 现状 | 影响 | 对应规约 |
|---|---|---|
| Flask 开发服务器、默认密钥、无部署产物 | 无法直接公开服务 | 003 WEB-002/008 |
| 登录跳转 /，JS 却从 URL 读取 user_id | 正常登录后首页不加载业务 | 003 WEB-001 |
| 公开 JSON 输入类型不校验、无 CSRF/生产限流 | 公网输入可触发错误和查询注入 | 003 WEB-003/004/005 |
| run_mining complexity=17；关键词/KMeans 在 Spark，cosine 在 driver | 核心相似度边界与宪法不完全一致 | 后续 001 修订；004 新实现用 Spark |
| _collect_rows 与 baseline complexity=14；Web 逐篇查询最新结果 | 大规模请求延迟待实测 | 性能改进与答辩压测 |
| 文档记录过 9,986 条预验收，但最终万条主源证据仍缺 | 不得宣称规模验收已通过 | 答辩完成门槛 |
| 002 把排序差异当效果对比，没有人工相关性数据 | 不能证明推荐质量提升 | 人工 Precision@K/NDCG 评价 |
| 无播客采集/订阅/通知模块 | 新需求需要完整链路 | 004 |
| 本次环境 Mongo 未启动、无 Docker；有 Java/PySpark | 容器/真实库验收需另行运行 | 验收记录逐项注明 |

[方案] 优先顺序：公网与交互可用性 → 播客增量处理 → 大规模与评价证据 → 更新报告和答辩演示。
[待确认] 外网部署资源、截止日期、课程是否强制多机集群、目标播客与自动转写预算。


## 本轮实现后的复核

[已验证] 修改后再次通过 codegraph index_directory 更新索引，共 31 个运行源文件。新增 podcasts 子包 6 文件，工具识别 23 函数；解析/网络边界复杂度最高（parse_transcript=21、parse_feed=20），相关安全/格式测试已覆盖本轮支持范围。

[已验证] codegraph index_markdown 同样因 Memory manager not initialized 失败，无法执行依赖文档存储的 verify_design。规约与代码的一致性改用人工契约核对、测试与实际 HTTP/浏览器验收；未声称自动设计验证已成功。

[已验证] 本轮修复登录后页面不加载、输入类型/CSRF/限流缺口、文献相似度 driver 计算与逐文档查询问题；新增播客完整切片和人工指标工具。保留真实规模、标签和云环境未完成事项。
