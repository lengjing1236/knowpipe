# 011 验证顺序

[方案] 精确命令及实际结果由实施完成后更新，以下执行顺序已冻结。

1. 阅读 spec.md 和根目录 plan.md；冻结 cases.json 与原文来源摘要，不依据新算法输出改预期。
2. 使用 scripts/prepare_semantic011.py 和 scripts/prepare_language011.py 下载有界本地模型并保存来源／SHA；无付费服务。
3. 先运行契约／单元测试，再给本地模型独占 CPU／内存窗口，记录中文目标和全文 A/B。
4. 小真实案例运行词汇基线及新算法、无历史／无去重消融，逐项对原文事实判断。
5. 原10,215篇背景运行同场景与冻结保留场景，未知输出单列待复核。
6. scripts/acceptance_mvp011.py 启动隔离 Mongo 数据库、Flask、真实 worker 和浏览器；不得写入伪 ready result，测试目标、阅读、已读重算、增量通知。
7. evidence/011-mvp-recommendation-validation/acceptance.md 分开记录工程测试和 SC 产品结果，更新 tasks.md／plan.md。未通过项不得勾作效果完成。

资源：只在本机执行 Spark；并行开发不等于同时启动多个 Spark／翻译大任务。验收结束关闭自启 worker／浏览器并清理隔离数据库。
