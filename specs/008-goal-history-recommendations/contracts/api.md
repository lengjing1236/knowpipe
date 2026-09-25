# 推荐接口

[方案] 沿用 /api/learning 会话和 CSRF，拒绝请求指定 user_id。

- GET /recommendations：返回 status、reason、input_revision、corpus、历史对照计数及 items；仅当前账户。未启动 worker 明确说明。
- POST /recommendations：JSON {}，记录刷新意图，返回相同状态契约（202）；不执行 Spark。
- 目标和已读操作沿用 007，worker 按 revision 自动补建任务；同输入与同语料不重复入队。
- items 含 source/doc_id/content_version、title、relevance、history_overlap、rank、reason、goal_evidence、history_evidence 及 chinese_ready/translation_status。证据是绑定版本的原文切片，不由模型编写。
- 无目标、未配置模型、无语料不构造假结果。成功只表示计算完成，未宣称内容补充正确率或学习效果。

[方案] 内部 importer 必须要求 fulltext_verified=true、source/doc_id/title/body_text/language/source_url/license；仅处理本地 JSONL，不把网络输入开放给 Web。事务级全文质量不由非空校验代替，正文采集的来源验证独立记录。

[已验证] 保存新目标或修改已读后，网页立即移除旧卡片，再读取新状态；即使状态请求失败，也不继续展示上一输入的结果。轮询请求采用递增序号，较早响应不能覆盖新请求。
