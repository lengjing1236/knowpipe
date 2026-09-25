# Feature 004 契约

[方案] 下列接口必须登录，修改请求还需要 CSRF；用户 ID 从 session 获取，不允许指定他人。

- GET /api/podcasts/subscriptions → {items: [{feed_id, url, title, last_checked_at, last_error}]}。
- POST /api/podcasts/subscriptions {url} → 201/200 {feed_id}；非法地址 400、订阅超额 409；网络抓取由 worker 进行。
- DELETE /api/podcasts/subscriptions/{feed_id} → 200 {}；幂等，不删除共享结果。
- GET /api/podcasts/episodes → {items: [{episode_id,feed_id,title,status,published_at,batch_id,error_code}]}，限 50 条，限本人订阅。
- GET /api/podcasts/episodes/{episode_id} → {episode,...,transcript,analysis}；不存在或非本人订阅 404。
- POST /api/podcasts/episodes/{episode_id}/transcript {text} → 202 {status: queued}；仅 awaiting_transcript/failed 可用，否则 409。
- GET /api/notifications → {items: [{id,episode_id,title,created_at,read}]}，最近 50 条，限当前用户。
- POST /api/notifications/{id}/read → 200 {}；非本人 404。
- GET /api/notifications/stream → text/event-stream；event: notification、id: Mongo ObjectId、data: JSON。按 Last-Event-ID 恢复，空闲发送心跳、约 25 秒后重连；无游标初次连接只读最近 50 条，页面以通知 id 去重。

错误为 {error: 稳定代码}，不得回显数据库凭据、网络底层异常或其他用户信息。
