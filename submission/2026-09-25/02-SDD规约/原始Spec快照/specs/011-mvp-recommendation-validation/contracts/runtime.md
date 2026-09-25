# 011 共享运行契约

状态：[方案] 2026-09-25 冻结第一版；接口新增须兼容旧调用。具体错误码在实现时补齐，不能放宽结果含义。

## 语义 provider 与推荐

`recommend(spark,index,goal,history,*,query_plan=None,semantic=None)`。

semantic 支持 `relevance(query,passages)`、`encode(sentences)`、`infer(premise_hypothesis_pairs)`，以及可复现 processor_id；具体返回数据结构由算法模块定义并加测试。多语rank/embed直接使用原中文，semantic.language标记multilingual；英文NLI对中文证据必须显式翻译，provider.text为可注入EnglishText转换器，保留原文对应关系。无 provider／失败不产生补充资格。

结果增加 `semantic`：status（ready/unavailable/failed/partial）、processor_id、comparison_scope（实际候选／历史句数与上限）、error_code。item 延续原文 evidence/history_evidence，新增 `comparison`（covered/possible_supplement/uncertain/goal_only，双侧证据与分数组成）。`supplement_eligible` 仅候选补充证据通过时为 true。旧词汇基线必须明确不具备语义补充判断；RSS 不得复用旧 eligible。

模型窗口超限不得静默截断。所有跨度针对原始全文，显示时核对版本。语义特征与补充分数参与 Spark 最终选择，并保留消融项。

## 语言

`TextResult(text,language,complete=True,segments=())`；segments 为列表／元组：`{source_start,source_end,target_start,target_end,kind:'text'|'protected'}`。如提供对齐，必须连续覆盖整个原文与译文，跨度合法且真实对应翻译单元；缺少对齐返回空，不推测。

`content_view(...include_text=True)` 增加 translation_segments。质量字段保留 `semantic_verified:false`、`human_reviewed:false`；数字添加／丢失、标识符丢失是完整性风险，不是语义评分。术语、条件和关系准确性由真实输出事实对照另证。

双向模型可共享实例。processor_id 包含模型文件与适配规则，旧缓存不可误用。旧提供器无 segments 仍兼容，但不能作为新对齐验收依据。

## Worker／API／Web／RSS

worker 显式配置 semantic，并把其 processor_id 纳入 processing_identity；变更产生新任务，租约／generation 保持。API 透传有限非敏感状态；Web 用中文解释当前比较范围、可能补充和不确定，不显示用户掌握程度断言。

目标保存只排队，真实 worker 计算；已读更新使 revision 变化重新排队。完整中文准备失败可阅读原文但不伪称中文就绪。RSS 有历史时要求新语义补充通过与中文就绪；模型失败或重复材料不发补充通知。冷启动依旧只能称目标相关。

## 后续模型适配（开发实测触发）

NLLB未通过技术语义A/B，保留可选实验配置。新增显式loopback LlamaTranslator候选，支持固定GGUF身份、服务模型/上下文预检、实际分词窗口分段、非完整生成拒绝及真实位置对齐；采用固定通用翻译指令，不对验收句单独修正。是否可用于最终验收须等待Qwen真实输出，而非工厂配置优先级。
