# 真实多源全文语料验收

2026-09-25，执行的是公开来源采集与全文完整性核验，没有使用生成文本充当学习文档。

**最终规模验收使用后续扩充冻结版：10,215 篇、5 个原始提供方，路径 `state/feature009/corpus-expanded/documents.jsonl`。本文件记录保留不动的 10,179 篇基础冻结版；扩充内容、最终哈希与核验见 [sources-expanded.md](sources-expanded.md)。**

## 实际结果

已冻结 **10,179 篇**有效、去重后的计算机技术文档：10,167 篇完整问答主题帖、7 篇 Python 官方文档、5 篇 Django 官方文档。问答中的 **15,751 条回答**附属于其问题，不额外计作文档。原始内容提供方是 Stack Exchange、Python Software Foundation、Django Software Foundation，共 3 个；Stack Exchange 的不同社区不重复计为独立提供方。

| 技术社区 | 完整问答篇数 |
| --- | ---: |
| Stack Overflow：编程与开发 | 1,653 |
| DBA：数据库 | 1,735 |
| Unix：Unix/Linux | 1,689 |
| Server Fault：系统与网络运维 | 1,709 |
| Software Engineering：软件工程 | 1,650 |
| Computer Science：计算机理论 | 1,731 |

正文共 40,390,215 字符、40,626,517 UTF-8 字节，297,623 个非空自然段，最大单篇正文 69,526 字节。自然段不是 Spark 索引中最终满足分词条件的段落数量，也不能单独计作文档。标签覆盖 SQL Server、PostgreSQL、算法、Linux、Python、复杂度、C++、Bash、Java、设计模式、网络、Docker、Kubernetes 等；没有把 Pandas 当成整个项目的范围。

语料文件位于 `state/feature009/corpus/documents.jsonl`，逐页原始响应与下载来源在同目录 `responses/`。全部规范记录按文件顺序的 SHA-256：

```text
dd31832bfe87c4371c3e8f67c76297eee831c449a2fa00641b7a235ab37fdf51
```

机器可读统计见 [corpus.json](corpus.json)，独立核验见 [corpus-verification.json](corpus-verification.json)。原始全文留在被忽略的 `state/`，没有将整批第三方内容提交到 Git。

## 如何取得与判定完整

Stack Exchange [官方问题类型](https://api.stackexchange.com/docs/types/question)允许在问题响应中包含回答。采集使用固定自定义过滤器同时请求问题和全部回答的 HTML，按六个实际技术领域轮换分页；依据[官方高级搜索参数](https://api.stackexchange.com/docs/advanced-search)，选择有采纳答案、未关闭、未迁出的新近问题，再排除负分题。

必须满足：问题正文存在；返回回答数等于平台声明的 `answer_count`；采纳答案确实在返回集合里；每条答案身份和所属问题正确且正文非空。整帖通过才发布为全文，缺失不能改成摘要或截断后继续计数。清洗保留段落、代码缩进与图片引用，按来源身份和完整正文哈希去重。本次拒绝了 733 条不符合负分/关闭/迁出条件的候选，没有回答缺失或截断后冒充全文的情况。

按照[官方限流规则](https://api.stackexchange.com/docs/throttle)保留统一配额、`backoff` 和 HTTP `Retry-After`，每个社区最多使用 19 页，未超过匿名 25 页上限。正式初采 107 次请求，重放缓存并补齐数量又使用 2 次，共 **109 次**；最终 API 仍报告剩余配额 187。`corpus.json` 中 `network_requests=2` 是最后一次重放调用的联网数，不是整轮只请求了两次。初采记录另存于 [corpus-initial-collection.json](corpus-initial-collection.json)。

Python、Django 文档复用 Feature008 已取得的真实 HTML 响应，先核对原始 HTML 哈希，再用明确的主内容容器提取，保留原始 URL 和原取得时间。来源入口为 [Python 中文文档](https://docs.python.org/zh-cn/3/)与 [Django 中文文档](https://docs.djangoproject.com/zh-hans/5.2/)。没有将导航页、答案片段或文档段落拆成额外独立文档。

## 验证

```bash
python3 scripts/prepare_fulltext_corpus.py --target 10100 --max-requests 180 --output state/feature009/corpus
python3 -m knowpipe.corpus.verify --root state/feature009/corpus
python3 -m unittest tests.corpus.test_sources tests.recommendations.test_importer -v
```

14 项本域测试通过。独立核验脚本重新读取缓存原始 API 回包，对全部 10,179 条记录检查规范字段、身份唯一和总体哈希，对问答核对全部 15,751 条原始答案及逐帖作者覆盖，并确认 **27,851 个代码块**在提取正文中原样保留。核验不是只读取采集器写下的 `fulltext_verified` 标记。

导入使用现有版本化正文接口：

```bash
python3 -m knowpipe.recommendations.importer --input state/feature009/corpus/documents.jsonl --mongo-db knowpipe_feature009
```

采集分支没有擅自更改现有数据库；统一数据库导入与 Spark 全链路运行由主分支集成验收记录负责。

## 明确边界

- 12 篇官方文档只是第二类真实内容的起点，数量明显少于问答，尚不代表所有领域都有系统教程与官方说明覆盖。
- 最新的已采纳问题是一个有偏采样。不同社区活跃度不同，软件工程覆盖到 2021 年，数据库到 2023 年；年份范围已经逐领域写入统计。采纳标记和非负分都不是正确性、时效性或学习效果标签。
- 1,462 篇含外部图片引用，图像内容未 OCR；评论没有包含在问答文字正文定义中。阅读与推荐不能声称系统已经理解这些图示。
- 全文分析使用原始语言：10,167 篇英文问答、12 篇中文官方文档。这份验收不证明英文翻译质量；翻译由入选推荐后的另一条处理链负责。
- [官方许可政策](https://stackoverflow.com/help/licensing)按贡献/修订版本区分 CC BY-SA 许可。本次 API 未返回 10,068 条问题的具体许可版本，已明确保存“版本未返回”及原文、作者链接和政策入口；回答则保留实际返回许可，没有统一伪写为 4.0。
- arXiv HTML 适配器已有固定版本、显式许可和正文结构检查，但本次规模语料未纳入 arXiv，也没有实现批量 PDF/OCR 回退。旧 arXiv 摘要仍不算全文。
- 以上验证证明数据实际存在、完整性规则和代码保留有效；不证明推荐相关性、个人补充价值、中文翻译或学习效果。
