# 最终多源语料与覆盖补充

2026-09-25。最终冻结文件：`state/feature009/corpus-expanded/documents.jsonl`。

| 原始内容提供方 | 有效全文文档数 | 内容职责 |
| --- | ---: | --- |
| Stack Exchange | 10,167 | 六个实际计算机技术领域的完整问题与全部答案 |
| Python Software Foundation | 7 | Python 数据结构、异常、SQLite、异步与日志等官方说明 |
| Django Software Foundation | 5 | 事务、查询、聚合、优化与异步等官方说明 |
| PostgreSQL Global Development Group | 24 | PostgreSQL 18 事务隔离、锁、索引、执行计划、分区、WAL和备份 |
| Docker, Inc. and contributors | 12 | 容器网络驱动、存储卷、挂载、资源限制、日志和无根模式 |
| **合计** | **10,215** | **5 个原始提供方，48 篇官方说明** |

新增内容针对基础库已有的 PostgreSQL、Docker、Linux、网络等问题。例如基础库中 `postgresql` 标签有 551 篇、`docker` 有 122 篇，补充资料可以提供与这些问题相关的官方规则与边界说明。这只是来源与主题适配依据，不代表每条问答都已经建立正确的跨来源关联。

正文 40,868,017 字符、41,104,683 UTF-8 字节，300,806 个非空自然段；最大单篇正文 69,526 字节。最终文件 SHA-256：

```text
ecf155020ecb4c0813632f7b523608be5b08a9b073cb7b7aa64861e2ed24a37e
```

完整统计与 36 个新增页面的 URL/标题清单见 [corpus-expanded.json](corpus-expanded.json)。

## 可复现方式

```bash
python3 scripts/prepare_fulltext_corpus.py --target 10100 --output state/feature009/corpus
python3 -m knowpipe.corpus.supplement --base state/feature009/corpus --output state/feature009/corpus-expanded
python3 -m knowpipe.corpus.verify --root state/feature009/corpus-expanded
python3 -m knowpipe.recommendations.importer --input state/feature009/corpus-expanded/documents.jsonl --mongo-db knowpipe_feature009
```

补充器先核验基础文件的哈希和文档数，禁止原地覆盖或将输出嵌套在基础目录内。原始 `state/feature009/corpus/documents.jsonl` 仍为 10,179 篇，SHA-256 仍是 `dd31832bfe87c4371c3e8f67c76297eee831c449a2fa00641b7a235ab37fdf51`。两版的继承关系写在扩充清单的 `base_snapshot`；基础记录不重复计数。

原始网页与来源元数据均留在扩充目录的 `responses/`。PostgreSQL 固定使用 18 版；Docker 官方站没有统一的软件版本号，记录为 `unversioned`，以获取时间和响应哈希固定页面内容，不虚构一个软件版本。

## 来源、许可与完整性

PostgreSQL 页面只提取 DocBook 的主节或命令说明容器，Docker 页面只提取正文 `article.prose`。导航与站点外框不计入学习正文，表格单元有明确分隔，代码缩进和全部正文保留。

PostgreSQL 的[官方许可页面](https://www.postgresql.org/docs/18/legalnotice.html)明确覆盖软件及文档；完整许可 HTML 已保存为 `postgresql_docs-LICENSE.html`。Docker 使用其[官方文档仓库许可证](https://github.com/docker/docs/blob/main/LICENSE)，为 Apache-2.0；因 raw 主机连接超时，改从同一官方仓库的公开 Contents API 获取，固定许可证 blob 为 `f8971197c6f5ba9d1c7b9d8f4b6198ec5a885596`，解码后的完整许可保存为 `docker_docs-LICENSE.txt`。各文档记录原作者/项目、许可 URL、原始响应哈希和本地许可证副本哈希。

Docker 站点出现过连接重置，采集按缓存恢复，单个公开文档请求最多重试两次瞬时网络错误。限流、权限拒绝、配额耗尽不会通过重试绕过，`Retry-After` 会持久化。最终清单的 `network_requests=10` 仅指最后一次恢复调用，不能解释为全部补充只使用了十次请求。

## 验证与边界

[独立核验记录](corpus-expanded-verification.json)验证了全部 10,215 条记录的身份/哈希，15,751 条原始答案的完整性和作者覆盖，27,851 个问答代码块及 779 个官方文档代码块的原文保留，以及 48 篇官方文档响应来源；新增许可副本也核验了哈希。19 项本域测试通过，命令与范围见 [corpus-tests.json](corpus-tests.json)。

官方文档已经覆盖数据库和容器系统，但它们的数量仍显著少于问答，不代表课程目录或每个技术领域都覆盖完整。采纳标记、正文完整和来源可靠也不等于内容永远正确。图片仅保留引用，未进行 OCR；此次数据验收不证明翻译质量、个性化推荐收益或学习效果。跨来源关联是否准确，由后续算法及业务验收另行检查。
