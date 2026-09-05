# Phase 0 Research: 个性化知识分类与 Web 展示

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档解决 Technical Context 中未被调研文档直接固定的技术细节。凡调研文档已经
决定的事项（MongoDB/Flask 选型本身、四态判定的两层信号来源、评价方式使用
Precision@K/NDCG 或人工评分）不在此重复论证。

## 1. 登录鉴权实现方式

**Decision**: 不引入 Flask-Login 等第三方鉴权库，直接用 Flask 自带的
`session`（服务端签名 cookie，存 `user_id`）配合 Werkzeug 自带的
`generate_password_hash`/`check_password_hash` 做密码哈希与校验。以一个
`@login_required` 装饰器包裹所有涉及用户个人数据的路由（画像读写、推荐结果、
反馈提交），装饰器校验 `session["user_id"]` 存在且对应的用户在 `users` 集合
中确实存在；越权访问（如请求携带的 `user_id` 参数与 session 中的不一致）时
直接拒绝（403），不静默改写请求参数。

**Rationale**: 项目宪法未要求生产级安全合规，用户已明确"登录鉴权系统还是需要
的，生产级别并发可以不用"——即需要真实的账户和会话隔离，但不需要企业级
鉴权框架的完整功能（OAuth、MFA、限流等）。Flask + Werkzeug 已是既定依赖
（Primary Dependencies），不新增依赖即可满足"真实登录、真实会话隔离"的要求，
符合"不为假设的未来需求引入抽象"的实现原则。

**Alternatives considered**: Flask-Login——提供更完整的 `current_user`/记住我
等能力，但对本项目规模是过度设计，且新增一个第三方依赖；JWT 无状态鉴权——
更适合前后端分离场景，但本 feature 是服务端渲染单页，session cookie 更简单
直接，不需要客户端管理 token。

## 2. 分类判定与推荐分数的计算位置

**Decision**: 两者拆开但共享同一批次证据：
- **分类判定**（known/refine/new/possible_conflict）：对每个用户、每个知识
  单元做集合匹配（关键词是否命中已知关键词集合、主题簇是否命中已知主题簇
  集合），计算量是 O(用户知识单元数)，用纯 Python 规则函数
  （`knowpipe/web/classify.py`）实现，不经过 Spark。
- **推荐排序分数**：`score = α×主题相关度 + β×新增关键词比例 + γ×文档质量
  - ε×已读/重复惩罚`（来源多样性 δ 项留待有多来源共现数据后再引入，本阶段
  两个来源规模差异大，暂不计入，避免分数被数据量而非内容质量主导），对
  全量文档批量计算，用 PySpark DataFrame 操作实现（`knowpipe/web/score_job.py`），
  产出带 `batch_id` 的运行统计，与 Feature 1 的批次证据模式一致。

**Rationale**: 项目宪法 Principle I 只要求"核心挖掘计算"（TF-IDF/KMeans/
相似度/推荐分数）必须由 Spark 产出且可检查；分类判定属于"用已产出的关键词/
主题簇做用户特定的集合比较"，不属于该清单内的计算类型，且判定结果依赖单个
用户的画像（用户数量、画像变更频率高于文档规模），用 Spark 对全量文档 ×
全量用户做批处理会引入不必要的复杂度。但推荐排序分数是纯粹基于文档内容
（不依赖用户）的批量计算，落在宪法明确列出的"推荐分数"一项，必须由 Spark
产出，因此保留为独立 Spark 作业。

**Alternatives considered**: 把分类判定也放进 Spark 作业——被拒绝，因为
判定逐用户变化，Spark 作业通常按"全量文档一次批处理产出一个批次结果"设计，
强行把用户维度拉进 Spark DataFrame 会让每次用户设置新已知主题都要重跑 Spark
作业，与 spec SC-004"修改画像后判定状态实时反映最新画像"的目标冲突。

## 3. possible_conflict 判定证据来源

**Decision**: 冲突候选生成使用调研文档 5.7 节允许的 LLM 辅助边界——当一个
知识单元命中用户已知主题簇、且该知识单元的关键词与用户画像中对应主题的
"已确认认知"存在字面矛盾迹象时（如同一关键词在不同文档中被赋予相反的
技术结论，需要具体文本比对），由 LLM 生成一条候选冲突说明及引用片段，但
该候选必须挂上 `pending_review: true` 状态，在人工复核确认前，系统对外
API/页面一律不展示为 `possible_conflict`（按 FR-003 降级展示为 `new` 或
`refine`）。人工复核通过后，该记录的状态才切换为对外可见的
`possible_conflict`，并保留复核人与复核时间作为可追溯证据的一部分。

**Rationale**: 项目宪法 Principle I 明确"LLM 输出禁止作为核心挖掘成果替代
Spark 计算"，但允许 LLM 用于"'可能冲突'候选生成"这一辅助环节；同时 Principle
II 要求 possible_conflict"仅在有可追溯断言和判定证据时展示，并必须经人工
复核后才可对用户可见"。把 LLM 限定为"生成候选、人工复核前不可见"，同时满足
两条原则，且避免把课程周期内难以做到高精度的自动化语义冲突检测，误当作
可以直接展示给用户的最终结果。

**Alternatives considered**: 完全人工浏览全部文档标记冲突——在课程周期和
数据规模下不可行；纯规则判定（如关键词共现但主题簇不同）——容易产生大量
误报且缺乏可读的冲突依据文本，不满足"可追溯断言"的要求；跳过 possible_conflict
状态、只做前三态——被拒绝，因为这会丢弃 spec 已明确要求的功能（FR-003 及
User Story 边界），不是本 feature 范围内可自行删减的项。

## 4. 非个性化基线的具体定义

**Decision**: 基线排序采用"纯相似度排序"：对用户已读文档集合中的每篇文档，
取 Feature 1 挖掘结果中 `similar_doc_ids` 关联的文档，按相似度顺序展示，
不做任何已知/新知识过滤或分层；若用户没有已读文档，退化为按 `created_at`
倒序的"最新文档"列表。两种情况都不读取用户的已知主题集合，代表"不做个性化
判定，只用内容相关性或新鲜度排序"的对照组。

**Rationale**: 调研文档 6 节要求"至少设置'热门度/纯相似度'作为非个性化
基线"；由于当前数据源（Stack Exchange 问答、arXiv 摘要）没有真实的浏览量/
点赞数可作为"热门度"信号，纯相似度基于 Feature 1 已产出的 `similar_doc_ids`
字段直接可用，不需要额外计算，且与"个性化"版本使用同一份挖掘结果，能公平
对比"加入个性化判定"与"不加入"两者的效果差异（FR-014/016，SC-003）。

**Alternatives considered**: 用 Stack Exchange 的 `score`/`view_count` 字段
构造热门度基线——可行但引入来源特定字段（arXiv 无此类字段），会让两个来源的
基线定义不一致，留到后续若有余力再补充；随机排序作为基线——过于弱，无法
体现"个性化排序是否比合理的非个性化方案更好"这一课程要求的对比意图。

## 5. 单用户范围重算的性能路径 [待验证]

**现状**：用户修改已知主题或提交反馈后，需要重新判定该用户涉及的知识单元
状态（spec SC-004），这是单用户范围的计算（只涉及该用户已产出的
`user_knowledge` 记录和该用户新画像），预期不需要重跑 Spark 作业，可以在
Flask 请求处理过程中用纯 Python 直接完成重算并写回 MongoDB。

**Decision**: 用户画像变更后的重算路径为"Flask 路由直接调用
`classify.py` 中的判定函数，对该用户名下的 `user_knowledge` 记录做批量
`update_many`"，不触发 Spark 作业；Spark 作业（`score_job.py`）只在文档
规模变化（Feature 1 产出新批次）或需要重新计算全量推荐分数时运行，与用户
画像的实时性解耦。

**Rationale**: 对应 research.md 第 2 节的计算位置划分；避免把"用户点一次
按钮"这种交互操作绑定到 Spark 作业启动开销（Spark 会话启动本身有数秒级
固定成本），保证 SC-004 的"数秒内返回"目标可行。

**Alternatives considered**: 每次画像变更都重跑一次 Spark 作业——被拒绝，
启动开销会让交互体验不可用，也违背"数秒内返回"的性能目标；异步任务队列
（如 Celery）延迟重算——被拒绝，属于本项目规模不需要的额外基础设施复杂度，
课程周期内没有必要引入。**待验证项**：实际接入真实 ≥10,000 条规模数据后，
需要确认纯 Python 批量 `update_many` 在该规模下是否仍能在数秒内完成单用户
重算；若发现瓶颈，再考虑对 `user_knowledge` 查询范围加索引优化或分页处理，
不在本阶段提前设计。

## 6. 测试策略：Flask 契约测试与 mongomock

**Decision**: API 路由测试用 `app.test_client()` 直接发起请求，不启动真实
HTTP 服务器；数据库依赖用 `mongomock` 注入替身，覆盖鉴权拒绝、越权拒绝、
四态判定结果、possible_conflict 降级、反馈更新画像等场景。规模化验证
（≥10,000 条、Spark 推荐分数作业）仍需对接真实 MongoDB 实例与真实 `local[*]`
Spark 会话，不能用测试替身的结果代替课程证据，与 Feature 1 research.md
第 4 节确立的原则一致。

**Rationale**: 与 Feature 1 保持测试策略一致性，降低两个 feature 之间的
认知切换成本；Flask `test_client()` 是标准做法，不需要额外测试依赖。

**Alternatives considered**: 用 `pytest` + `pytest-flask` 替换现有
`unittest`——被拒绝，仓库现有测试全部基于 `unittest`（Feature 1 沿用同一
选择），中途切换框架增加不必要的迁移成本，不在本 feature 范围内。

## Resolved Technical Context

以上六项已覆盖 Technical Context 中所有需要决策而调研文档未直接给出的细节，
无遗留 NEEDS CLARIFICATION 项；第 5 节标注的"单用户重算性能"留待接入真实
规模数据后验证，不阻塞当前设计推进。
