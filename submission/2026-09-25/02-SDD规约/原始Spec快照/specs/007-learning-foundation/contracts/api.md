# 学习工作台接口契约

[方案] 路由前缀 /api/learning；全部要求有效会话，写入沿用 X-CSRF-Token 与 JSON 对象校验。身份取 session；JSON 或 query 若带 user_id 则拒绝 400，避免客户端误认为可以指定账户。

| 方法及路径 | 输入 | 成功输出 |
| --- | --- | --- |
| GET /profile | 无 | goal、revision、read_count |
| PUT /goal | `{text}`，1–1000 字符 | 更新后的 profile |
| GET /documents | page≥1、limit 1–50（默认20）、source 可选、q 标题子串≤100字符 | items、page、limit、total；按 source/doc_id 稳定排序 |
| GET /document | source、doc_id | 单篇内容视图及 is_read、read_version_status |
| GET /history | page、limit | 已读 items 及分页；含 read_at、read_content_version、version_status；已删除资料保留占位 |
| PUT /read-state | `{source, doc_id, read: boolean}`；read=true 时必须额外提交页面所展示的 content_version（无正文为 null） | source、doc_id、is_read、revision |

[方案] source 是 1–64 位字母／数字／下划线／连字符来源标识；doc_id 为 1–512 字符，禁止控制字符，允许斜线。未知 JSON 字段拒绝。GET 资料缺失 404；标记不存在资料 404；撤销已删除资料成功 200。参数错误 400，未登录 401，CSRF 缺失 403。错误返回 `{error: 稳定代码}`，页面映射中文。

[方案] 新端点仅访问已有公开 documents。分页不返回原文全文、内部错误详情或其他账户数据。每项包含来源、标识、标题、链接、完整性、语言、处理状态及自身已读状态。is_read 表示读过某版本，版本状态单独为 current / changed / unknown / unavailable。

[方案] 标记时展示版本与当前正文不一致，返回 409 `{error: content_changed}`，要求重新打开后再标记，不替用户把旧阅读标成新版已读。校验后发生的并发正文更新也不会更换所记录指纹。分析阶段的当前 queued/running/failed/unavailable 状态优先于历史 mining 指针，防止旧结果掩盖当前失败。

## 内部提供方契约

[方案] Transcriber.transcribe(audio_path) 与 Translator.translate(text, source_language, target_language) 返回 TextResult(text, language, complete)。默认提供方抛 ProviderUnavailable。失败／部分结果不得发布成功；translation 的 source_version 从调用开始固定，完成时再核对。真实模型在后续阶段配置。
