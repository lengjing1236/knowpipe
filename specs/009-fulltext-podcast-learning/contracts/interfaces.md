# Shared Contracts
[方案] 保持现有API兼容，新增字段仅可选。
1. importer JSONL原必填字段不变；新增来源字段由采集器核验并透传，不以缺许可版本伪造许可。正文最大4,000,000字节，超限明确拒绝，不截断。
2. Translator.translate(text,source_language,target_language)->TextResult；Transcriber.transcribe(Path)->TextResult，text/language/complete。新增 local_providers.configured_translator(env=None)、configured_transcriber(env=None)，无模型返回unavailable；无隐式下载/付费。
3. podcasts.audio.transcribe_url(url,transcriber,limits=AudioLimits())->TextResult：公网校验、流式限额、时长探测、本地文件协议限制、清理临时文件。默认128MiB/2小时可配置；不把部分转写当完整。
4. recommend返回保留旧字段，新增supplement_evidence（见data-model）及supplement_eligible。算法不负责通知；业务层额外验证当前个人修订、文档版本、订阅、中文全文。无历史可发goal_only目标推荐；有任何显式已读历史时，主动通知须supplement_eligible。
5. RSS处理后用source=podcast/doc_id=episode_id发布完整文档；发布方文字稿优先，否则音频转写。幂等版本发布，新的音频任务遵守租约失效保护。个人通知由推荐worker在成功发布当期结果后生成。
6. Web延用 GET /api/podcasts/episodes、/api/notifications，并在学习页面展示状态；通知不把document标记已读。证据展示原文且明确原文身份。

## 实现后的边界补充（2026-09-25）
[已验证：测试] RSS 允许16MiB feed（规范化正文仍以4,000,000字节为上限），publisher文字稿失败且有音频时回退ASR；缺模型不下载音频。显式 `POST /api/podcasts/episodes/<id>/retry` 为本人订阅的失败任务重新开启最多3次重试。
[已验证：测试] 文档翻译重试按正文版本最多3次、间隔5分钟；POST推荐刷新清除当前入选且未就绪文档的重试计数。读取通知不标记文档已读。
[已验证：测试] 小批变更阈值15%、累计30%、最多8代；新增词出现频率超过25%时完整重建。重写合并Parquet仍有全量IO，但旧文档分段/词权重不重算。可配置LEARNING_MAX_PARAGRAPHS，默认1,000,000；不截断正文来过限额。
[方案边界] 有任何显式历史但没有可比相关片段时，普通列表仍可按目标推荐，主动通知要求补充依据；真正无历史才能发goal_only通知。

[已验证：修复后的契约] 缓存复用验证paragraphs/terms/postings的完成标记及数据文件；损坏则重建。任务身份包含特征基底签名，语料返回以前版本时stale任务重新排队。迟到的发布方文字稿可恢复失败音频任务；第三次翻译中断不永久停在running，显式刷新可恢复。

## 可诊断的翻译失败
API 的 processing.translation 在 failed/unavailable 时仅透传预定义 error_code：空输出、未正常结束、不完整、输入/输出非法、超时、模型缺失、不支持语言、次数耗尽或通用失败。未知异常正文不写入响应；内容版本变化后旧错误不继续显示。Web 提供中文解释和刷新重试入口，不发布部分译文。
