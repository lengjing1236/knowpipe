# 公开数据集适用性调研报告

调研日期：2026-09-03。  
本轮只读取官方 API、数据集页面、数据字典和许可证说明，没有下载大型数据集，也没有修改仓库文件。

## 1. 评价标准

按以下标准判断：

1. **真实性与可获取性**：是否由原始发布者或可信机构发布，能否通过公开下载或 API 获取。
2. **规模**：是否有可核验的记录数、API 查询总数或文件规模；没有证据时标记“规模未验证”。
3. **文档属性**：是否包含标题、正文、摘要、评论、代码注释等可处理文本。
4. **许可证**：是否明确允许教育用途、保存、修改、派生和展示；平台名称不等于许可证。
5. **Spark 适配性**：是否适合批量清洗、TF-IDF、关键词、主题、相似度或聚类。
6. **MongoDB 适配性**：是否能以文档形式保存原文、来源、标签、时间和挖掘结果。
7. **个性化适配性**：是否能根据标签、主题、关键词或用户兴趣构造推荐和知识差分。
8. **可答辩性与风险**：数据来源是否容易解释，是否存在版权、API 限流、字段缺失、重复和噪声问题。

报告中的判断分为：

- **已验证事实**：本轮实际读取页面或 API 得到的内容。
- **基于资料的推断**：根据字段、格式和规模作出的工程判断。
- **待确认事项**：需要教师、数据发布者或项目成员进一步确认。

## 2. 候选数据集详细调查

### 2.1 Stack Exchange Data Dump / Stack Exchange API

#### 名称与链接

- Stack Exchange API： [Stack Exchange API](https://api.stackexchange.com/)
- Stack Overflow API 示例： [questions?site=stackoverflow&filter=withbody](https://api.stackexchange.com/2.3/questions?site=stackoverflow&pagesize=1&filter=withbody)
- 数据库结构说明： [Database schema documentation for the public data dump and SE API](https://meta.stackexchange.com/questions/2677/database-schema-documentation-for-the-public-data-dump-and-se-api)
- 数据转储入口： [Stack Exchange Data Dump](https://archive.org/download/stackexchange)
- 内容许可说明： [Stack Overflow licensing](https://stackoverflow.com/help/licensing)

#### 原始来源

[已验证事实] Stack Exchange 网络及其各个问答站点。API 返回的记录包含 `question_id`、`title`、`body`、`tags`、`score`、`creation_date`、`accepted_answer_id`、`content_license` 等字段。

#### 规模证据

[已验证事实] 2026-09-03 API 返回：

| 站点 | 问题数 | 答案数 |
|---|---:|---:|
| Stack Overflow | 24,249,994 | 36,102,348 |
| Server Fault | 329,147 | 522,660 |
| Super User | 516,635 | 753,329 |
| Ask Ubuntu | 425,414 | 540,329 |

仅 Stack Overflow 一个站点就远超 1 万条。API 返回的示例记录中包含正文和 `content_license`。

[待确认] Archive.org 数据转储页面本轮受到网络访问限制，未核实当前转储文件大小、最新日期和 XML 文件清单。因此，转储的具体规模不能仅依据本轮访问确认。

#### 字段与样例

转储通常按 `Posts.xml`、`Users.xml`、`Comments.xml` 等 XML 文件组织。帖子字段包括：

- `Id`
- `PostTypeId`
- `ParentId`
- `CreationDate`
- `Score`
- `ViewCount`
- `Body`
- `OwnerUserId`
- `Title`
- `Tags`
- `AnswerCount`
- `CommentCount`
- `AcceptedAnswerId`
- `ContentLicense`

API 示例中的正文是 HTML，例如：

```json
{
  "question_id": 7978620,
  "title": "What's a portable way to implement no-op statement in C++?",
  "body": "<p>...</p>",
  "tags": ["c++", "no-op"],
  "content_license": "CC BY-SA 3.0"
}
```

#### 许可证与合规

[已验证事实] API 记录带有 `content_license` 字段，实际记录可出现 CC BY-SA 3.0 或 CC BY-SA 4.0。

[推断] 使用时需要：

- 保存原始链接、作者标识和许可证；
- 遵守署名和 ShareAlike 条件；
- 不能把所有历史帖子简单标成同一个版本的 CC BY-SA；
- 对删除内容、用户信息和个人数据进行过滤。

[待确认] 课程是否允许把经过清洗的帖子正文存入 MongoDB 并在 Web 页面展示；还应确认使用 API 还是官方转储。

#### 获取难度

- API：低到中。无需 API Key 即可测试，但有配额、分页和请求间隔限制。
- Data Dump：中到高。需要下载并解析大型 XML，且本轮无法确认 Archive.org 当前可用性。
- API 记录默认不一定包含正文，需要使用合适的过滤器。

#### Spark 适配性

[推断] 很高。适合 XML/JSON 批量解析、HTML 清洗、标签统计、TF-IDF、关键词提取、主题聚类和问答关联分析。

#### MongoDB 适配性

[推断] 很高。可以按文档保存 `source_site`、`post_id`、`post_type`、`title`、`body_html`、`body_text`、`tags`、`score`、`created_at`、`accepted_answer_id`、`content_license`、`source_url` 和挖掘结果。

#### 挖掘与个性化适配性

[推断] 很高。可以根据用户已掌握的标签或主题，推荐同标签但不同站点的问题、与已读问题相似的新问题、用户未接触过的相关标签，以及低重复度且具有高质量答案的问题。

#### 主要风险

- HTML、代码块、链接和引用噪声；
- 同一问题的修订版本和重复内容；
- 删除帖子不可见；
- 许可证版本和署名要求；
- API 限流；
- 多站点字段基本相似但并非完全一致。

#### 结论

**推荐。**

这是目前最适合作为主数据源的候选。API 的规模和字段已经验证，正文技术内容明确，多站点天然体现异构性。若课程允许使用转储，批处理效率更高；否则可先使用 API 建立受控规模数据集。

### 2.2 CodeSearchNet

#### 名称与链接

- [CodeSearchNet 官方仓库](https://github.com/github/CodeSearchNet)
- [CodeSearchNet README](https://github.com/github/CodeSearchNet/blob/master/README.md)
- [CodeSearchNet 论文](https://arxiv.org/abs/1909.09436)

#### 原始来源

[已验证事实] GitHub 与 Microsoft Research 共同发布，数据来自开源代码库。数据集面向自然语言代码检索。

#### 规模证据

[已验证事实] 官方 README 明确写明：

- 约 200 万条 `comment`–`code` 对；
- 支持 Python、JavaScript、Ruby、Go、Java、PHP 六种语言；
- 预处理数据约 3.5 GB；
- 数据按 train、validation、test 划分。

规模远超 1 万条。

#### 字段与样例

官方 JSONL 字段包括：

- `repo`
- `path`
- `func_name`
- `original_string`
- `language`
- `code`
- `code_tokens`
- `docstring`
- `docstring_tokens`
- `sha`
- `partition`
- `url`

典型记录是一个函数及其自然语言注释：

```json
{
  "repo": "owner/repository",
  "path": "src/example.py",
  "func_name": "get_vid_from_url",
  "language": "python",
  "code": "def get_vid_from_url(...): ...",
  "docstring": "Extracts video ID from URL.",
  "url": "https://github.com/..."
}
```

#### 许可证与合规

[已验证事实] 官方仓库代码和文档采用 MIT，但 README 明确说明：数据中源代码的许可证随语言和原始仓库提供，存放在 `_licenses.pkl` 文件中。

因此：

- 不能把整个数据集简单标成 MIT；
- 每个源仓库需要保留原始许可证；
- 课程展示代码片段时应保留仓库链接和许可证；
- 需要确认课程是否允许公开展示派生后的代码文本。

#### 获取难度

中等。官方脚本从 S3 获取约 3.5 GB 数据，并依赖 Docker/模型环境；只使用 JSONL 数据本身时可以减少依赖，但仍需取得数据文件。

#### Spark 适配性

高。适合按语言和仓库批处理、函数注释清洗、代码与文档词频统计、TF-IDF、关键词、注释与代码相似度以及按语言或主题聚类。

#### MongoDB 适配性

高。函数级记录天然适合 MongoDB 文档存储，可保存代码、注释、语言、仓库和许可证信息。

#### 挖掘与个性化适配性

中到高。适合根据用户掌握的语言推荐新的 API 或函数，根据已读注释推荐相似代码文档，或发现“已掌握 Python、未接触 Go”的跨语言内容。

但它更接近“代码检索数据集”，不是 Stack Overflow 一类的完整技术问答文档。

#### 主要风险

- 许可证高度异构；
- 代码注释质量差异大；
- 重复代码、模板代码和自动生成代码；
- 主要是英文；
- 代码体积大于普通文本；
- 需要进行安全过滤，不能直接执行代码。

#### 结论

**有条件推荐。**

适合做特色补充源或代码知识分支，但不建议在许可证和数据获取方式未确认前作为唯一主数据源。

### 2.3 OpenAlex Works 元数据

#### 名称与链接

- [OpenAlex API](https://api.openalex.org/)
- [OpenAlex Works API 示例](https://api.openalex.org/works?filter=default.search:Apache%20Spark,has_abstract:true&per-page=1)
- [OpenAlex 数据文档](https://docs.openalex.org/)

#### 原始来源

[已验证事实] OpenAlex 由 OurResearch 维护，收录全球学术出版物元数据。

#### 规模证据

[已验证事实] API 查询结果显示：

- 搜索 `Apache Spark` 的作品约 30,922 条；
- 其中带摘要的作品约 28,695 条；
- 按计算机科学相关概念筛选时，返回数量达到千万级。

这是动态 API 结果，不是固定版本快照。

#### 字段与样例

API 记录包含 `id`、`doi`、`title`、`publication_year`、`publication_date`、`language`、`authorships`、`topics`、`keywords`、`cited_by_count`、`open_access`、`primary_location` 和 `abstract_inverted_index`。

OpenAlex 的摘要常以 `abstract_inverted_index` 形式返回，需要根据词位置重建文本。例如：

```json
{
  "id": "https://openalex.org/W2542459869",
  "title": "Apache Spark",
  "language": "en",
  "abstract_inverted_index": {
    "This": [0],
    "open": [1],
    "source": [2],
    "computing": [3]
  }
}
```

#### 许可证与合规

[待确认] OpenAlex 数据的具体许可政策应以当前官方文档和服务条款为准。作品元数据、摘要和论文全文并不具有同一版权状态。

必须区分 OpenAlex 自身整理的元数据、出版商提供的摘要、论文全文及 PDF，以及论文原始开放许可。不能因为记录可通过 API 查询，就默认允许保存和展示全文。

#### 获取难度

- API：低到中，适合按主题分页获取；
- 全量快照：高，数据规模很大，不适合短周期课程项目；
- API 查询和快照访问政策需要查看当前配额及服务条款。

#### Spark 适配性

高。适合论文标题和摘要批处理、主题和关键词统计、引用关系分析、按年份和主题进行趋势统计，以及技术主题相似度计算。

#### MongoDB 适配性

高。元数据字段结构稳定，适合存储论文文档、主题、作者和开放获取状态。

#### 挖掘与个性化适配性

中到高。适合根据用户掌握的主题推荐相邻研究主题、新发表论文、引用较少但主题相近的论文，以及从工程问答到学术论文的知识扩展。

缺点是摘要缺失、全文缺失，个性化结果主要依据标题和摘要。

#### 主要风险

- 元数据与全文版权不同；
- 摘要经常缺失；
- 摘要是倒排索引格式，需重建；
- API 结果动态变化；
- 快照太大；
- 论文主题并不总是工程实践内容。

#### 结论

**有条件推荐。**

适合作为学术元数据补充源，不宜单独承担“完整技术文档正文”要求。

### 2.4 arXiv Computer Science API 元数据

#### 名称与链接

- [arXiv API Basics](https://info.arxiv.org/help/api/basics.html)
- [arXiv API 查询示例](https://export.arxiv.org/api/query?search_query=cat:cs.*&start=0&max_results=1)
- [arXiv 许可证说明](https://info.arxiv.org/help/license/index.html)

#### 原始来源

[已验证事实] arXiv 由 Cornell University 维护，是开放预印本平台。

#### 规模证据

[已验证事实] 2026-09-03 的 API 查询 `search_query=cat:cs.*` 返回 `opensearch:totalResults = 931692`，计算机科学类别的元数据规模远超 1 万条。

#### 字段与样例

Atom XML 记录包括 `id`、`title`、`summary`、`author`、`published`、`updated`、`category`、`primary_category`、`comment`，以及 PDF 和摘要页面链接。示例记录包含题目、摘要、作者、发布日期和 `cs.*` 分类。

#### 许可证与合规

[已验证事实] arXiv 许可证页面明确写明：所有 metadata 适用 Creative Commons CC0 1.0 Universal Public Domain Dedication。

但这不等于论文全文全部是 CC0：

- 论文作者可选择 CC BY、CC BY-SA、CC BY-NC 等许可；
- 某些论文可能受出版商版权约束；
- 课程系统应优先保存元数据和摘要；
- 若展示 PDF 或全文，必须逐条检查论文许可证。

#### 获取难度

中等。API 无需登录即可查询，但官方要求控制请求频率；大规模分页获取 90 万条记录需要较长时间，也需要进一步确认当前批量元数据访问限制。

#### Spark 适配性

高。适合对标题、摘要和分类字段执行 TF-IDF、主题分析、关键词抽取、相似度计算和时间趋势分析。

#### MongoDB 适配性

高。每篇论文可以作为一个文档保存，元数据结构稳定。

#### 挖掘与个性化适配性

高。可以根据用户已掌握的技术主题推荐新论文、邻近分类、主题相似但用户未读过的论文，以及从 Stack Overflow 工程问题延伸到学术研究。

#### 主要风险

- 摘要长度和质量差异较大；
- 同一论文可能有多个版本；
- 预印本不一定经过同行评审；
- 全文许可逐篇不同；
- API 请求频率限制；
- 学术论文与日常工程问题之间存在语域差异。

#### 结论

**推荐作为补充数据源。**

元数据许可证清晰，计算机领域规模大，适合与 Stack Exchange 问答形成“工程实践 + 学术研究”的异构来源。

### 2.5 Crossref Works API

#### 名称与链接

- [Crossref REST API](https://api.crossref.org/)
- [Crossref Works 查询示例](https://api.crossref.org/works?query.bibliographic=Apache%20Spark&rows=1&select=DOI,title,author,published,abstract,license,link,subject)

#### 原始来源

[已验证事实] Crossref 维护 DOI 注册元数据，由出版商和学术机构提交记录。

#### 规模证据

[已验证事实] 本轮查询以 `Apache Spark` 检索返回约 43,331 条结果；2024-01-01 至 2024-01-02 的全部 Works 查询返回 1,656,158 条结果。

这些数字代表 Crossref 检索结果，不等于技术文档数量。

#### 字段与样例

常见字段有 `DOI`、`title`、`author`、`published`、`container-title`、`subject`、`abstract`、`license`、`link` 和 `publisher`。样例记录中可以看到题目、作者、出版年份、DOI、许可证链接和 PDF 链接。

#### 许可证与合规

[已验证事实] Crossref 记录的 `license` 字段按作品分别提供，且可能为空。

因此：

- 不能把 Crossref 全部数据标成 CC BY 或 CC0；
- 只能保存和展示明确许可的元数据或摘要；
- DOI 元数据与论文全文版权必须分开处理。

#### 获取难度

低到中。API 可直接访问；建议使用 `mailto` 参数进入 polite pool；大量分页仍需控制频率；不需要先下载大型快照。

#### Spark 适配性

中到高。适合元数据和摘要统计，但摘要缺失率可能较高。

#### MongoDB 适配性

高。记录结构适合文档存储，DOI 可以作为唯一键。

#### 挖掘与个性化适配性

中等。适合标题、摘要和主题字段的关键词与相似度分析。若没有摘要，只能进行标题级推荐。

#### 主要风险

- 记录重复；
- 题目、作者和日期格式不统一；
- 摘要经常缺失；
- 许可证字段不完整；
- 技术领域筛选需要自行设计；
- 不能把检索总数当成技术正文规模。

#### 结论

**有条件推荐作为补充源。**

适合补充 DOI、出版信息和引用入口，不适合作为唯一文本主源，也不建议把 Crossref 检索总数直接计入 1 万条技术文档。

### 2.6 Kaggle：Python related StackOverflow posts

#### 名称与链接

- [Python related StackOverflow posts](https://www.kaggle.com/datasets/xiangjerryhe/python-related-stackoverflow-posts)
- [Kaggle API 元数据](https://www.kaggle.com/api/v1/datasets/view/xiangjerryhe/python-related-stackoverflow-posts)

#### 原始来源

[已验证事实] Kaggle 发布者为 Xiang Jerry He。数据说明称其来源为 Stack Overflow Posts XML，并筛选 2008—2018 年帖子、至少 2 个赞、有已接受答案，且文本中提到 Python 或包含 Python 标签。

#### 规模证据

[已验证事实] 数据集大小为 154,848,404 字节；Kaggle 页面最后更新时间为 2024-03-22；Kaggle 页面显示许可证为 CC BY-SA 4.0。

[规模未验证] Kaggle API 元数据没有提供记录数和文件字段清单，因此不能确认是否达到 1 万条。

#### 字段与样例

发布说明只确认来源为 Stack Overflow Posts XML，没有在当前 API 元数据中提供完整数据字典。

[待确认] 需要通过 Kaggle 文件预览或发布者说明核对具体文件名、帖子字段、正文、标签、答案、许可证和实际行数。

#### 许可证与合规

[已验证事实] Kaggle 页面显示 CC BY-SA 4.0。

[待确认] 由于数据来自 Stack Overflow，仍需核对原始帖子的 `content_license`，不能只依赖 Kaggle 页面标签。展示时要保留 Stack Overflow 链接和署名。

#### 获取难度

中等。Kaggle 通常需要账号或 API Token；数据文件约 155 MB，课程项目可接受，但不应在调研阶段下载；数据已经筛选为 Python 内容，减少了清洗范围。

#### Spark 适配性

高。若行数达到 1 万条，适合 Spark 文本处理和 Python 主题分析。

#### MongoDB 适配性

高。Stack Overflow 帖子结构适合 MongoDB 文档。

#### 挖掘与个性化适配性

高。Python 主题明确，可以根据用户已知标签推荐相邻知识，例如 pandas、NumPy、异步编程和 Web 框架等。但它基本是 Stack Overflow 的单来源子集，不能单独体现多源异构。

#### 主要风险

- 记录数量未验证；
- 可能存在重复和旧版本内容；
- 许可证继承关系需要核实；
- 只覆盖 Python，主题多样性不足；
- Kaggle 页面可能长期不更新。

#### 结论

**有条件推荐。**

适合快速原型或备用数据源，但在确认记录数和原始许可证之前，不应作为课程最终主数据源。

### 2.7 GH Archive

#### 名称与链接

- [GH Archive](https://www.gharchive.org/)
- [GitHub event types](https://docs.github.com/en/webhooks-and-events/events/github-event-types)

#### 原始来源

[已验证事实] GH Archive 项目记录 GitHub 公共时间线，将事件按小时归档。

#### 规模证据

[已验证事实] 归档从 2011-02-12 开始；2015 年起使用 GitHub Events API；数据以小时为单位的 `.json.gz` 文件提供；网站说明包含 15 种以上事件类型。

[规模未验证] 官方页面没有给出本项目可直接使用的固定事件条数，因此不能声称具体有多少条技术文档。

#### 字段与样例

记录是事件 JSON，字段随事件类型变化，常见字段包括 `type`、`created_at`、`actor`、`repo` 和 `payload`。Issues、IssueComment 等事件的 `payload` 中可能包含标题、正文或评论，但不是每条事件都有完整文本。

#### 许可证与合规

[待确认] GH Archive 页面说明数据来自 GitHub 公共时间线，但没有在本轮页面中找到明确的数据集许可证。

“公开事件”不等于可以无条件保存、修改和公开展示其中的用户内容。还需核对 GitHub 服务条款、仓库许可证和个人信息处理要求。

#### 获取难度

中等。可按小时直接获取 gzip 文件，但文件数量和总体积很大，事件 schema 异构，需要过滤出 Issue、Discussion 或评论事件。

#### Spark 适配性

高，特别适合事件流批处理、时间趋势和仓库活跃度统计。

#### MongoDB 适配性

高，事件 JSON 可以原样存储。

#### 挖掘与个性化适配性

中等。适合推荐活跃项目、技术标签和仓库活动，但不适合作为稳定的技术文档语料库。

#### 主要风险

- 文本只存在于少数事件；
- 事件字段高度异构；
- 没有明确统一许可证；
- 可能包含用户名、仓库路径等个人或组织信息；
- 事件量大但有效技术正文比例未知。

#### 结论

**不推荐作为主数据源；有条件作为活动元数据补充。**

### 2.8 B 站技术视频字幕/转写

本轮没有发现一个由 B 站官方发布、具有固定版本、明确许可证和可验证记录数的技术字幕开放数据集。

[已验证事实] B 站视频和字幕属于公开内容来源，不等于开放数据集。

[待确认] 需要确认视频作者是否授权下载和再分发字幕、B 站 API 或页面抓取是否允许批量使用、字幕来源和质量，以及是否允许保存到 MongoDB 并在课程 Web 页面展示。

**结论：不推荐作为当前课程主数据源。**

### 2.9 RSS 技术博客/博客文章

RSS 是一种内容获取方式，不是固定版本的数据集。每个博客的版权、更新频率、历史文章数量、正文是否包含在 feed 中以及是否允许批量保存和再发布，都需要逐站点确认。

**结论：不推荐作为满足 1 万条规模的主数据源。** 可以作为少量实时补充源，但不应把 RSS feed 数量或公开可见文章直接视为开放数据规模。

### 2.10 UCI、Google Dataset Search、Papers With Code、AWS Open Data、天池、百度飞桨

这些是数据目录或平台，不是单一数据集。

本轮没有找到一个同时满足以下条件、且能够直接核验的具体技术文档数据集：明确原始发布者、明确许可证、至少 1 万条技术文本、可以保存和修改、能够课程展示。

因此，本报告不把这些平台名称当作候选数据集。若要使用，必须先指定具体数据集页面并重新核对许可证、规模和字段。

## 3. 候选数据集对照表

| 数据集 | 来源类型 | 文本规模 | 语言 | 许可证 | 获取难度 | Spark 适配性 | 技术内容相关性 | 多源价值 | 最终结论 |
|---|---|---:|---|---|---|---|---|---|---|
| Stack Exchange API / Data Dump | 技术问答社区 | 已验证：单站点问题数超过 2,400 万 | 英文为主，多站点 | 帖子记录带 CC BY-SA 版本字段 | API 中等，Dump 中高 | 高 | 高 | 高 | 推荐 |
| CodeSearchNet | GitHub 开源代码及注释 | 已验证约 200 万对 | 英文为主，6 种编程语言 | 每个源仓库许可证不同 | 中等 | 高 | 高，但偏代码 | 高 | 有条件推荐 |
| OpenAlex Works | 学术元数据 | API 查询数万至千万级 | 英文及多语言 | 元数据许可需按当前 OpenAlex 条款确认 | API 中等，快照高 | 高 | 中到高 | 高 | 有条件推荐 |
| arXiv cs.* 元数据 | 学术预印本 | 已验证约 931,692 条 | 英文为主 | 元数据 CC0；全文许可逐篇不同 | 中等 | 高 | 高 | 高 | 推荐为补充 |
| Crossref Works | DOI 元数据 | 检索结果数万级以上 | 多语言 | 记录级许可不统一 | 低到中 | 中到高 | 中 | 中 | 有条件推荐 |
| Kaggle Python related StackOverflow posts | Kaggle 二次整理 | 记录数未验证，文件约 155 MB | 英文 | Kaggle 显示 CC BY-SA 4.0，原帖需复核 | 中等 | 高 | 高 | 低 | 有条件推荐 |
| GH Archive | GitHub 事件归档 | 总量很大，但文档条数未验证 | 多语言 | 数据集许可证未找到 | 中等 | 高 | 中 | 高 | 不推荐主用 |
| B 站字幕 | 公开内容来源 | 未验证 | 中文为主 | 未明确 | 高 | 中 | 高但不稳定 | 高 | 不推荐 |
| RSS 技术博客 | 内容获取方式 | 未验证 | 多语言 | 逐站点确认 | 低到中 | 中 | 中到高 | 高 | 不推荐主用 |

## 4. 主数据源与补充数据源建议

### 主数据源：Stack Exchange API 或官方 Data Dump

负责满足至少 1 万条技术文档、真实正文和标签、多站点异构、Spark 批量清洗和文本挖掘，以及 MongoDB 文档存储。

优先顺序：

1. 教师允许时，使用官方 Data Dump；
2. 若 Dump 获取受限，使用 Stack Exchange API 分页采集；
3. 先锁定 Stack Overflow、Server Fault、Super User、Ask Ubuntu 等站点；
4. 保存 `content_license`、来源 URL 和采集日期。

### 补充数据源：arXiv 元数据

用于体现工程问答与学术论文的来源差异，以及摘要、主题分类和发布时间字段。arXiv 元数据许可证清晰，且 API 规模已验证；但如果只有标题和摘要，应明确说明它是“元数据/摘要文档”，不能夸大为论文全文语料。

### 可选特色补充：CodeSearchNet

用于体现自然语言注释与代码的关系、跨编程语言知识发现，以及“用户已掌握一种语言、推荐另一种语言”的个性化场景。但必须逐条处理源代码许可证，开发和答辩风险高于 arXiv。

### 不应计入最终 1 万条规模的来源

除非教师明确认可，否则不建议将以下内容计入“1 万条技术文档”：GH Archive 中没有正文的事件、Crossref 中没有摘要的元数据记录、OpenAlex 中只有标题和 DOI 的记录、RSS feed 条目、B 站视频 URL 或播放记录、记录数尚未核验的 Kaggle 数据，以及只有下载量或文件大小而没有记录数的数据。

## 5. 推荐的数据组合

### 方案一：稳妥方案

**Stack Exchange API/转储 + arXiv CS 元数据**

| 项目 | 判断 |
|---|---|
| 数据量 | Stack Exchange 已验证远超 1 万条；arXiv cs.* 已验证约 93 万条元数据 |
| 许可证风险 | Stack Exchange 需保留 CC BY-SA 署名和 ShareAlike；arXiv 元数据 CC0，全文许可仍逐篇区分 |
| 开发工作量 | 中等 |
| 课程匹配度 | 高：问答正文、摘要、标签、时间、来源均可进入完整链路 |
| Spark 价值 | HTML 清洗、TF-IDF、关键词、主题和相似度 |
| MongoDB 价值 | 保存问答和论文摘要等异构文档 |
| 多源价值 | 工程问答 + 学术论文 |
| 答辩风险 | 较低，但需解释“摘要文档”和“全文”的区别 |

### 方案二：特色方案

**Stack Exchange + CodeSearchNet**

| 项目 | 判断 |
|---|---|
| 数据量 | 两者均超过 1 万条 |
| 许可证风险 | 较高，CodeSearchNet 的源代码许可证按仓库变化 |
| 开发工作量 | 中高 |
| 课程匹配度 | 高，技术问答 + 代码注释 + 代码文本 |
| Spark 价值 | 文本与代码联合处理、跨语言主题分析 |
| MongoDB 价值 | 问答文档和代码文档混合管理 |
| 多源价值 | 很高 |
| 答辩风险 | 中高，容易被追问许可证、代码展示和数据清洗规则 |

## 6. 推荐的最小数据集

当前最适合启动的是：

**Stack Exchange API 多站点技术问答 + arXiv Computer Science 摘要元数据。**

理由：

1. 两个 API 本轮都已实际访问成功；
2. Stack Exchange API 已验证有数千万级问题和答案；
3. arXiv `cs.*` 已验证有 931,692 条记录；
4. 两者字段差异明显，能够证明多源异构；
5. Stack Exchange 正文适合技术问答挖掘；
6. arXiv 摘要适合主题扩展和新知识发现；
7. arXiv 元数据 CC0，合规边界比混合许可证代码更清楚；
8. 不需要一开始下载数百 GB 的全量快照；
9. 可以先获取受控样本，再决定是否扩展到 1 万条以上。

最小启动范围建议：

- Stack Exchange：先选 Stack Overflow、Server Fault、Super User、Ask Ubuntu；
- 每个站点获取带正文的问题记录；
- arXiv：只取计算机科学分类下带摘要的记录；
- 统一字段：`doc_id`、`source`、`title`、`body_or_abstract`、`tags_or_categories`、`language`、`created_at`、`source_url`、`license`；
- 只有在许可证、配额和课程认可确认后，才扩大规模。

这只是数据组合建议，不代表当前仓库已经具备 Spark、MongoDB 或 Web 运行环境。

## 7. 尚需人工确认的事项

1. 教师是否批准 Stack Exchange API/转储和 arXiv 元数据作为课程数据源，是否要求使用官方转储而不能使用 API？
2. 课程要求的“1 万条文档”是否接受 arXiv 标题+摘要元数据，还是必须全部包含完整正文？
3. Stack Exchange 内容是否允许在 MongoDB 中长期保存，并在课程 Web 页面展示？署名、ShareAlike 和删除请求如何处理？
4. Spark、MongoDB 和 API 的运行条件是什么，包括集群地址、Connector、API 配额和是否允许长期采集？
5. 个性化中的“已有知识体系”如何定义：用户选择标签、用户阅读历史，还是教师提供的知识标签体系？

## 8. 证据清单

| URL | 页面标题/用途 | 访问日期 | 结论类型 |
|---|---|---|---|
| [Stack Exchange API](https://api.stackexchange.com/) | API 首页 | 2026-09-03 | 已验证事实 |
| [Stack Overflow questions API](https://api.stackexchange.com/2.3/questions?site=stackoverflow&pagesize=1&filter=withbody) | 帖子字段、正文和 `content_license` 示例 | 2026-09-03 | 已验证事实 |
| [Stack Exchange info API](https://api.stackexchange.com/2.3/info?site=stackoverflow) | Stack Overflow 规模统计 | 2026-09-03 | 已验证事实 |
| [Stack Exchange info API — Server Fault](https://api.stackexchange.com/2.3/info?site=serverfault) | Server Fault 规模统计 | 2026-09-03 | 已验证事实 |
| [Stack Exchange info API — Super User](https://api.stackexchange.com/2.3/info?site=superuser) | Super User 规模统计 | 2026-09-03 | 已验证事实 |
| [Stack Exchange info API — Ask Ubuntu](https://api.stackexchange.com/2.3/info?site=askubuntu) | Ask Ubuntu 规模统计 | 2026-09-03 | 已验证事实 |
| [Stack Exchange schema documentation](https://meta.stackexchange.com/questions/2677/database-schema-documentation-for-the-public-data-dump-and-se-api) | 转储与 API 数据结构说明；本轮页面受 Cloudflare 保护 | 2026-09-03 | 待进一步核验 |
| [Stack Exchange Data Dump](https://archive.org/download/stackexchange) | 官方转储入口；本轮连接超时 | 2026-09-03 | 待进一步核验 |
| [Stack Overflow licensing](https://stackoverflow.com/help/licensing) | 内容许可说明；本轮页面受 Cloudflare 保护 | 2026-09-03 | 待进一步核验 |
| [CodeSearchNet README](https://github.com/github/CodeSearchNet/blob/master/README.md) | 规模、语言、JSONL 字段、S3 获取方式、许可证说明 | 2026-09-03 | 已验证事实 |
| [CodeSearchNet repository](https://github.com/github/CodeSearchNet) | 原始发布仓库 | 2026-09-03 | 已验证事实 |
| [CodeSearchNet paper](https://arxiv.org/abs/1909.09436) | 数据集背景 | 2026-09-03 | 基于资料的推断 |
| [OpenAlex Works API](https://api.openalex.org/works?filter=default.search:Apache%20Spark,has_abstract:true&per-page=1) | Apache Spark 相关作品数量、摘要字段示例 | 2026-09-03 | 已验证事实 |
| [OpenAlex API](https://api.openalex.org/) | OpenAlex API 入口 | 2026-09-03 | 已验证事实 |
| [OpenAlex documentation](https://docs.openalex.org/) | 数据和 API 文档入口 | 2026-09-03 | 待确认许可证细节 |
| [arXiv API query](https://export.arxiv.org/api/query?search_query=cat:cs.*&start=0&max_results=1) | cs.* 记录总数、Atom 字段示例 | 2026-09-03 | 已验证事实 |
| [arXiv API Basics](https://info.arxiv.org/help/api/basics.html) | API 获取方式和限制说明 | 2026-09-03 | 已验证事实 |
| [arXiv Licenses](https://info.arxiv.org/help/license/index.html) | 元数据 CC0 及全文许可说明 | 2026-09-03 | 已验证事实 |
| [Crossref Works API](https://api.crossref.org/works?query.bibliographic=Apache%20Spark&rows=1&select=DOI,title,author,published,abstract,license,link,subject) | Crossref 字段和许可证字段示例 | 2026-09-03 | 已验证事实 |
| [Crossref date query](https://api.crossref.org/works?filter=from-pub-date:2024-01-01,until-pub-date:2024-01-02&rows=0) | 某日期范围记录总数 | 2026-09-03 | 已验证事实 |
| [Kaggle dataset page](https://www.kaggle.com/datasets/xiangjerryhe/python-related-stackoverflow-posts) | Kaggle 数据集页面 | 2026-09-03 | 已验证页面存在 |
| [Kaggle dataset API metadata](https://www.kaggle.com/api/v1/datasets/view/xiangjerryhe/python-related-stackoverflow-posts) | 发布者、文件大小、更新时间、许可证和描述 | 2026-09-03 | 已验证事实 |
| [GH Archive](https://www.gharchive.org/) | 小时级 GitHub 事件归档、格式和起始日期 | 2026-09-03 | 已验证事实 |
| [GitHub event types](https://docs.github.com/en/webhooks-and-events/events/github-event-types) | GitHub 事件类型说明 | 2026-09-03 | 已验证事实 |

总体判断：**Stack Exchange API/转储最适合作为规模主源，arXiv 元数据最适合作为合规且异构的补充源；CodeSearchNet 适合特色扩展但许可证风险更高。**
