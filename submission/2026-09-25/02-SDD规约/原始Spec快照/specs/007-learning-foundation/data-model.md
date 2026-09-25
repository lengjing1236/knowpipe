# 数据模型

[方案] 在现有集合内增量扩展，原文与模型输出不混用。

## user_profiles

- user_id：已有唯一索引；由会话获取。
- learning_goal：可空 `{text, updated_at}`，规范化文本 1–1000 字符。
- learning_revision：默认 0；目标实际变化／标记／撤销才原子递增。
- read_doc_ids：`{source, doc_id, read_at, content_version}` 数组。source/doc_id 为稳定身份，版本是标记时的正文指纹；旧引用无时间／版本返回 null。
- known_topics / known_keywords：保留旧功能，不由已读自动修改。

[方案] 标记用 `$not/$elemMatch` 限制同身份只插一次，`$push + $inc` 同文档执行；撤销用 `$elemMatch + $pull + $inc`。目标只写自身字段。重复操作不修改首次时间或 revision。

## documents

- 原 source、doc_id、source_url、title、body_text、language、mining 保留。
- content：`{kind: fulltext, version}`；版本为原文语言和文本的 SHA-256。未声明或校验不符不视为有效全文。
- processing：extraction / transcription / translation / analysis，各含 `{status, source_version, error_code?}`。
- 状态：not_requested、queued、running、ready、failed、unavailable、not_required；读取时可派生 stale / unverified。
- translation：`{text, language: zh, source_version, complete: true}`；仅完整同版本产物可读。

[方案] 全文发布重置旧翻译和分析状态并清除当前 mining 指针；相同全文重复发布不清除已有译文。翻译缓存复用只在版本相同时成立；结果通过 source/doc_id/content.version/body_text/language 条件写入，过期结果丢弃。状态 ready 必须有相应产物；失败可记录阶段状态，不能伪造完成。

## 对外内容视图

[方案] 返回 content_status、content_version、language、processing、chinese_ready；详情额外返回 original_text 和 chinese_text。资料目录分页不返回全文。旧 arXiv 摘要、Stack Exchange 问题正文显式标记；旧数据缺字段不是新的完整性证明。

## 后续边界

[待确认] 音频文件存储、模型提供方、语料全文采集、历史正文版本保存和跨主机部署。此处仅建立可复用契约，不写入任何付费服务配置。
