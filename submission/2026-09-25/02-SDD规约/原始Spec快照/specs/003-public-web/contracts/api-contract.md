# 新增/调整接口契约

[方案] 同源 JSON API；所有修改方法要求 GET /api/auth/csrf 返回的 csrf_token 放入 X-CSRF-Token。
生产环境始终执行；本地测试通过显式配置可关闭，浏览器始终使用 token。

- GET /api/auth/csrf → 200 {csrf_token}，Cache-Control: no-store。
- GET /api/auth/me → 200 {user_id, username, known_topics}；未登录 401。
- GET /api/stats → 200 {documents, sources: {source: count}, batches: [{batch_id,status,sources,input_count,valid_count,failed_count,started_at,finished_at}], podcast_episodes}。
- GET /health/live → 200 {status: ok}。
- GET /health/ready → 200 {status: ready} / 503 {status: unavailable}。
- 修改请求失败：403 {error: csrf_failed}；非法 JSON：400 {error: invalid_json}；限流 429 {error: rate_limited} + Retry-After。

已有接口继续支持 user_id，但必须与 session 一致。密码、连接串和批次 error_message 不进入公开统计。
