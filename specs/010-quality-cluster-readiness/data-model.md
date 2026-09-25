# Data Model
[已实现]
- goal_interpretations：key=sha256(original_goal,query_processor_id,query_rules_version)，original、translated、status、error_code、created_at；只缓存成功解释，失败允许后续恢复；不包含用户身份、阅读历史。
- GoalQuery：original、variants(原目标及可用英文解释)、status(native/translated/unavailable/failed)、processor_id、entities([{name,aliases}])。解释不是新的用户目标。
- recommendation_runtime.corpus.processing_id：目标提供方、译文提供方、目标/对象规则的指纹；任务id含此字段；变更使generation递增，旧任务不可继续作为当前结果。
- translation：原字段加processor_id、quality；文档translation_expected_processor记录当前要求。内容或处理器变化使旧译文失效，发布须匹配二者。
- quality：checks_version、status、issues有限代码；数字/代码检查通过不代表语义正确或人工审校。
- Spark runtime evidence：master、driver、executor id/host、task count、文件可见性、输入/算法指纹、耗时、single_host标记；不能从两个PID推定两台物理机器。
- translation_generation：单调递增的运行代，和预期处理器、正文版本共同限制发布；与模型身份不同，同一模型也可能参与不同语料运行代。
- goal_interpretations实际存储为{_id,query:GoalQuery,created_at}；query中variants保留原文和译文，不另存重复用户画像。
- processing_id另包含完整性检查规则版本；通知重算更新当前job/processing依据，保留原有read与created_at，避免一集反复新增未读通知。
