# 全文语料准备

在仓库根目录执行：

```bash
python3 scripts/prepare_fulltext_corpus.py --target 10000 --output state/feature009/corpus
python3 -m knowpipe.recommendations.importer --input state/feature009/corpus/documents.jsonl --mongo-db knowpipe_feature009
```

第一条准备本地 JSONL；第二条才导入指定数据库。采集失败或实际数量不足时，第一条命令返回非零退出码，具体原因和已取得的数量仍写入 `manifest.json`。导入使用现有全文版本发布接口，同一正文重复导入不使成功译文失效。

补充数据库与容器系统的官方规则，并输出独立冻结版：

```bash
python3 -m knowpipe.corpus.supplement --base state/feature009/corpus --output state/feature009/corpus-expanded
python3 -m knowpipe.corpus.verify --root state/feature009/corpus-expanded
python3 -m knowpipe.recommendations.importer --input state/feature009/corpus-expanded/documents.jsonl --mongo-db knowpipe_feature009
```

补充范围固定为 24 篇 PostgreSQL 18 官方页与 12 篇 Docker 官方页；不覆盖原 JSONL。新增页保留完整许可副本和来源哈希。`verify` 会独立检查原始响应、答案/作者覆盖、官方来源和代码保留，不能作为答案正确性或学习效果评测。

## 取得什么

- Stack Exchange 官方 API 提供一篇问题的完整正文与全部回答正文，以整个问答主题帖为一篇文档。只有返回回答数量与平台声明一致、采纳回答存在、正文齐全、问题未关闭或迁出且非负分时才进入语料。
- 轮流采集编程、数据库、Unix/Linux、系统与网络运维、软件工程、计算机理论六个社区。它们属于同一个原始内容网络，不能当作六个独立内容提供方。查询按发布时间倒序，不强行凑齐与目标无关的站点。
- Python 与 Django 官方文档提取明确的主正文区域，不将导航页或摘要当成全文。已有 Feature008 真实响应缓存可复用，但须通过原始 HTML 哈希校验。
- 段落和代码缩进保留，图片保留原始引用。评论没有纳入本文档定义；图片内容没有做 OCR。因此这里的“全文”是完整问题与解答的文字正文，不代表图示的视觉信息已经被理解。

`official.arxiv_html_record` 提供保守的 arXiv HTML 正文适配入口，必须给出固定版本和已核实的许可；Atom 摘要、仅摘要的 HTML、无主正文页面都会拒绝。当前默认规模采集不批量拉取 arXiv，也没有 PDF/OCR 回退。

## 可复核与恢复

`responses/` 保存响应原文、URL、取得时间和 SHA-256；`documents.jsonl` 保存规范化完整文档及作者、许可、来源域、质量与提取方式；`manifest.json` 给出有效文档、重复/拒绝原因、原始提供方、领域、标签、正文量和图片依赖统计。

重新运行同一命令优先重放已经核验的缓存，再继续缺少的页面，避免重复消耗网络请求。缓存代表这次冻结采集；要构建更新的时间快照，请使用新的 `--output` 目录。不同时间快照不能相加宣称新增独立文档。

请求有总预算，每站匿名最多 25 页，统一处理平台配额与 `backoff`。遇到额度耗尽、HTTP 错误或缓存损坏时停止并记录，不自动改站点、改身份或突破分页限制。完整正文超过单文档 4,000,000 字节上限会明确拒绝，不截短后冒充全文。

许可逐帖记录。API 未返回问题的具体 CC BY-SA 版本时明确标为版本未返回，并保留官方政策地址、原文和作者链接。采纳标记与分数只是采样条件，不是答案正确性或学习效果标签。
