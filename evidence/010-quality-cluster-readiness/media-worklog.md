# 媒体实现记录（Feature 010）

- 2026-09-25：先运行新增 8 个处理器升级、并发旧任务、数字/代码遗漏、中英方向测试，旧实现产生 4 个失败、4 个接口缺失错误。
- 实现模型方向、确定性模型指纹、处理器/正文 CAS、公开有限质量状态后，扩展为 13 个新增测试。与原媒体测试合计 41 个测试通过（python3 -m unittest tests.learning.test_translation_quality tests.learning.test_local_providers tests.learning.test_content tests.learning.test_provider_errors，0.07 秒）。
- 原缓存测试的模拟译文删除了源代码，修正模拟输出保留代码；没有放宽完整性门槛。
- 数字检查保守要求阿拉伯数字及百分号保持（允许全角、千分位形式），数字写成汉字可能被阻止。检查不会发现“减少了 20%”误译为“减少至 20%”、技术术语误译、一般语义遗漏；通过不代表语义正确。
- 模型目录在单个 provider 生命周期内视为不可变；升级时创建新 provider / 重启 worker。指纹包含模型文件内容、方向、适配器版本，不暴露路径。
- 官方 zh_en 1.9 模型已下载，74,481,402 字节，SHA256 `62e7af5a3a48b530e47b7b3e5c78c2de79073ecd815750d2bf3ab35b4a67da2d`；来源与处理器 ID 见 goal-model.json。下载上限 160 MB、解压上限 512 MB，校验路径与 symlink，后续运行核对已观察 SHA。
- `python3 -m unittest discover -s tests/learning`：56 个测试通过，4.263 秒。新测试先失败后实现，实际推理独占资源窗口，与 Spark 串行。
- `python3 scripts/acceptance_media010.py`：8 条固定中文目标经真实 Argos zh_en 和 prepare_goal 完成，逐条记录在 goal-interpretations.json；完整 PostgreSQL 18 WAL introduction 的 2449 字符正文，经已有 Argos en_zh 翻译 4.564 秒，保留全部输入与输出至 translation-quality.json。
- 模型实际成功执行不表示翻译正确：具体错误见下文。本阶段没有更换英中模型，没有达成中文教学质量验收。

## 真实结果复核：仍有严重语义问题

以下为开发者对已观察结果的复核，不是独立人工评价集。没有修改查询或原始译文来隐藏失败。

| 固定目标 | 实际转换的问题 | 对推荐的可能影响 |
|---|---|---|
| PostgreSQL 的事务隔离级别 | transaction 被翻成 service | 原中文分支或 isolation 可能补救，英文目标表达仍不准确 |
| PostgreSQL 如何使用索引优化查询 | 意图大致保留，英文表达不自然 | 不据此断言检索有效，需真实检索记录 |
| Python 的异常处理机制 | 异常处理被翻成 Aberrant Mechanism | exception handling 丢失，英文召回可能严重受损 |
| Python 的异步任务与协程 | 异步/协程被翻成 Elevated / Processes | asynchronous/coroutines 丢失，不能说已理解目标 |
| Django 如何处理数据库事务 | transaction 被翻成 services | 可能偏向服务而非事务 |
| Django 如何防止跨站请求伪造 | 关键概念大致保留但英文搭配不自然 | 仍需检索确认 |
| Docker 容器如何持久化存储数据 | persistent 被翻成 Enduring，表达不自然 | 缺少关键术语可能降低英文召回 |
| 操作系统如何调度进程 | scheduling 被翻成 managing | 可能扩大为泛进程管理 |

WAL 全文通过本轮数字保留检查，但翻译仍不适合据此声称教学可用：

- 标题新增“将第28.3.节改为第28.3.节”，原文没有这一要求。这也说明仅检测数字缺失不会检测数字重复添加。
- `fsync` 被译为“一丝不苟”，丢失关键系统调用概念。
- transaction commit 被翻成“交易承诺”，数据库术语不准确。
- WAL 在标题中被译为“笔前记录”；journaling 等术语译法混乱。
- 原文 `data=writeback` 不在 Markdown 代码保护范围内，被翻为“数据=writeback”；代码完整性检查当前只覆盖有明确 Markdown 标记的代码。

因此 checks_passed 仅表示本轮有限完整性规则通过；human_reviewed=false 和 semantic_verified=false 必须持续展示。后续阶段 B 应先更换或比较模型、补齐术语与代码识别策略、建立固定技术文段评价，不能把本轮状态当作语义质量通过。


## 契约自查与补强

- 指纹实际把 `ADAPTER_VERSION`、source/target 方向写入 digest，再按稳定路径顺序读取 metadata、SentencePiece、CTranslate2 model 目录全部文件；新增断言验证权重、适配器版本、方向任意变化均改变 ID。同 provider 运行期不支持就地替换权重，已在运行文档写明。
- 译文成功发布、质量报告、失败状态均绑定正文内容与预期 processor；并发测试分别证明旧任务成功和失败都不能覆盖新任务结果。无身份旧发布方也不能覆盖已绑定处理器的正文。
- 另补齐直接 `publish_translation` 的入口：即使尚无预期处理器，显式传入 failed / 未知检查版本的质量报告也拒绝发布，避免绕过 `translate_document` 后成为 ready。新增测试通过。
- 数字匹配使用 Unicode NFKC，千分位/空格规范化，比较原文数字的计数是否缺失；代码仅按明确 Markdown 保护跨度作计数完整性检查。这些规则不证明顺序、上下文、术语或自然语言完整性。
- 成功复用需正文 ready、处理器相同、检查版本相同且 checks_passed；故意失败两次仍实际调用提供方，修复结果后第三次才发布成功。目标解释只写成功缓存由 root query 测试覆盖。
- 文档 `docs/local-media-providers.md` 已补中英模型下载、配置变量、独立只读挂载、超时、质量边界、升级重启约束，以及真实目标和 WAL 翻译的反例。
- 补强直接发布门禁后的 learning 回归：57 个通过，2.365 秒；随后新增失败缓存用例并扩展指纹断言，`python3 -m unittest tests.learning.test_translation_quality` 最终 15 个测试通过，0.031 秒。没有再运行重型模型或改写真实验收结果。

## 整合审查后的运行代数隔离

只读审查发现：旧 worker 在一篇模型推理期间丢失租约后，可能继续处理下一篇，并重新读取新 worker 的译文，再将预期处理器降回旧值；仅正文与处理器 CAS 不足以拒绝这种重新读取后的接管。最小复现中旧 worker 的第二篇最终 processor 为 old-model。

经 root 授权，媒体发布接口新增可选 `generation`，对应 runtime 的递增代数；claim 拒绝较低代数、无代数以及同代不同处理器接管。成功、失败与质量写入同时按 `translation_generation` CAS，阻止旧同处理器跨代写入。root 负责每篇处理前的租约/任务 guard。

新增两个用例覆盖“旧代重新读取仍不能接管”和“同处理器旧代无法发布成功/失败/质量”；相关内容、质量、失败码共 33 个测试通过，0.139 秒。真实模型验收结果不因本次并发修复重写，也没有重跑 Spark。

整合审查还复现并交 root 修复两处连接问题：

1. 目标解释失败后的显式刷新先移除 result，再读取原 items，导致全文翻译的三次失败预算未清零。root 改为先读取 previous_job，再重排任务并清零此前选中文档的预算。
2. 模型升级使旧 processing_id 通知不可见，但按 user+episode 的 $setOnInsert 不更新旧行，当前推荐无法恢复通知。root 改为更新当前推荐依据，同时只在首次插入设置 read/created_at；不新增未读。新增回归验证通知 ID、原已读状态和创建时间保留，当前版本重新可见，旧任务不能恢复旧依据。`python3 -m pytest tests/podcasts/test_learning_pipeline.py -q`：9 项通过，0.82 秒。

复读多语言召回实现：原目标与转换目标分别归一化并按各自词项分母计算覆盖率，同片段选择更高相关分支，未拼接语言分母；显式对象在候选截断前要求每个对象有标题或正文名称依据；仅按词汇和名称规则，不代表理解目标或判断语义新增。未发现新的接口遗漏，现有模型误译和覆盖不足保留为已知产品局限。
