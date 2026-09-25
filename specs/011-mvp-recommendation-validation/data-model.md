# 数据模型增量

[方案] Mongo 的 documents / user_profiles / recommendation_jobs / recommendation_runtime 继续沿用；不迁移历史原文或把打开行为变为已读。

- Translation：content_version、processor_id、generation、text、segments、quality。segments 原文／译文连续跨度，quality 明确语义未证实。模型变化不得使用旧翻译作为新结果。
- SemanticResult：status、processor_id、实际比较范围、有限错误码。模型/规则身份进入任务 processing_id。
- EvidenceComparison：候选／历史 source、doc_id、content_version、起止偏移、原文；相关性、覆盖、补充分量及状态。偏移不指向机器译文。
- EvaluationCase：id、topic、Chinese goal、冻结背景、history versions、expected source facts with spans、development/holdout 分组、代理编制 provenance；预期不来自算法分数。
- EvaluationRun：cases SHA、corpus SHA、模型 SHA、parameters、elapsed、每条结果与事实对照、pass/fail/unjudged、baseline/ablation。未判定不能算通过。

持久语义翻译缓存如实现必须绑定原文hash+provider身份，容量有上限；不缓存失败为成功。模型及大中间结果留 state/，小验收证据留 evidence/。
