# Feature 008 验收记录

[已验证] 2026-09-25，真实 MongoDB、Spark 与浏览器完成目标推荐、历史对照及中文阅读全文。使用隔离临时数据库，验收后清理，不覆盖项目旧语料。

## 实际材料与结果

[已验证] Python 官方中文文档 7 篇、Django 官方中文文档 5 篇，共 12 篇全文、2,345 个原文段落；涉及数据结构、异常、数据库事务、查询与异步并发。来源、许可、字符数和原文哈希见 [sources.json](sources.json)。原 HTML、全文及许可存于本地被忽略的 `state/feature008/sample/`；不把段落、译文或镜像作为独立文档。

| 场景 | 推荐数 | 本次结果 | 计算耗时 |
| --- | --- | --- | --- |
| 数据库事务／无历史 | 2 | sqlite3 --- SQLite 数据库的 DB-API 2.0 接口、数据库事务 | 38.31 秒 |
| 数据库事务／已读 SQLite | 1 | 数据库事务 | 14.1 秒 |
| 切换为 Python 异步并发 | 5 | 协程与任务、concurrent.futures --- 启动并行任务、异步支持、8. 错误和异常、同步原语 | 7.83 秒 |

[已验证] 上述为真实开发样例的流程结果；计时含本机执行成本，不是性能基准。首次目标门槛过宽，曾误荐只含“提交”的异步资料；改为目标词覆盖至少 50% 后重新验收，并加入回归例。该样例参与开发调整，不能充当独立效果评价。

## 运行与检查

[已验证] 18 项推荐测试、25 项学习功能测试、34 项原 Web 回归测试通过，共 77 项。推荐测试实际启动 Spark，涵盖同词异义、仅标题、开场白、镜像、原文引用、无匹配、空语料、缓存、历史版本、有限重试及过期令牌；翻译提供方测试使用明确的测试替身，不计作真实翻译。新增的“已有结果时仍显示语料超限失败”检查与相关队列/API 回归另见 [最终队列回归](queue-final-regression.log)。

[已验证] MongoDB 7.0.14 上 24 次并发领取仅 1 个 worker 成功。桌面 1440×1000 和手机 390×844 均完成推荐与阅读，无横向溢出、无页面脚本异常；主动标记才加入已读，输入修订、资料版本变化和账户隔离检查通过。主动模拟一次 HTTP 503，确认新目标保存后旧卡片仍立即清除；integration.log 中该 503 是预期故障注入。

[已验证] 关键证据：[结构化运行结果与基线/消融](result.json)、[真实运行日志](integration.log)、[Spark jobs](spark-jobs.json)、[Spark stages](spark-stages.json)、[桌面推荐](recommendations-desktop.png)、[已读对照](history-comparison-desktop.png)、[中文阅读](reading-desktop.png)、[手机版](recommendations-mobile.png)。Spark 使用 local[2]，索引复用情况与本次应用 ID 分别保留。只收集有界历史身份、候选标量和最终证据，不收集全库向量到 driver。

## 复现与边界

```bash
python3 scripts/prepare_recommendation_sample.py
PYTHONPATH=. SPARK_LOCAL_IP=127.0.0.1 python3 scripts/acceptance_recommendations.py --mongo-uri mongodb://localhost:27018
SPARK_LOCAL_IP=127.0.0.1 python3 -m unittest discover -s tests/recommendations -v
```

[已验证] 本次 Mongo 端口 27018 为专用本地验收实例；其他环境替换为可用实例。浏览器使用 @sparticuz/chromium 153.0.0 配合 Playwright，未关闭浏览器 Web 安全。JavaScript 语法、Python 编译、变更空白检查和 Compose YAML 结构通过；环境没有 Docker，未验证镜像构建或容器启动。

[待完成／整体要求] 12 篇只证明当前流程；万条有效全文、独立相关性/补充价值评价、通用跨语言语义、真实翻译、RSS 音频下载与 ASR、增量通知及跨主机集群未由本阶段交付。词汇重合可以减少明显重复，不能证明用户不懂某知识或已经掌握某知识。
