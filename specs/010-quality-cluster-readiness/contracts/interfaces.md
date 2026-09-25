# Contracts
[已实现，接口与验收边界见acceptance]
1. Translator.translate协议保留。LocalTranslator(model_path, source_language='en', target_language='zh', threads=2, timeout_seconds=1800) 支持所配置Argos方向，默认兼容旧调用；新增 configured_goal_translator(env=None) 读取KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH返回zh→en提供方，无隐式下载。
2. providers.processor_identity(provider)->str：确定性有限标识；真实本地模型包含模型文件指纹及适配器版本，缺配置保持明确标识，测试提供方有兼容路径。
3. providers.translation_matches(doc,provider)->bool；translate_document内部校验预期处理器/内容、质量，旧处理器译文不作为当前输出。publish_translation增加可选processor_id/quality参数，保持旧调用兼容。content_view公开translation_quality/translation_processor，不公开路径。
4. Root新增 prepare_goal(db, original, provider)->GoalQuery；recommend(spark,index,goal,history,query_plan=None) 可选参数兼容原调用。result.query解释传至Web；目标原文不被覆盖。
5. queue身份增加processing_id；runtime publish对其变更升代；worker重试预算同时绑定内容及译文处理器。词汇特征逻辑未变时不因目标模型变更重建全库。
6. runtime.create_spark(app_name,index_root,env=None)：尊重已有spark-submit与显式SPARK_MASTER；standalone校验共享目录并提供preflight工具；不得写入/读取用户凭据。
7. 质量issue公开枚举，前端中文说明。检测确定性缺陷不生成个人通知。字段新增不得破坏旧版全文/已读与RSSAPI。
8. translate_document 与内容 claim/quality/success/failure 增加可选 generation。生产worker传递递增runtime generation，逐篇检查租约及当前任务；文档拒绝低代及同代不同处理器接管，已绑定代的文档拒绝无代写入。防止丢租约旧worker重新读取新文档后降级。
9. 索引特征版本仍为text.ALGORITHM(v3)，排序实现使用engine.SELECTION_VERSION(v4)，评测恢复同时验证query.py指纹。模型/查询规则更新不强制重建未变化的词汇特征。
