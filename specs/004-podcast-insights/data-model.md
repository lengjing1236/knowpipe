# Feature 004 数据模型

[方案] 全部时间保存为 UTC datetime；API 输出 ISO 8601。

- podcast_feeds：feed_id（URL SHA256），url，title，last_checked_at，next_poll_at，last_error；feed_id 唯一。
- podcast_subscriptions：user_id + feed_id 唯一，created_at。
- podcast_episodes：episode_id（feed_id + GUID SHA256）唯一，feed_id，title，source_url，audio_url，transcript_url/type，published_at，status，attempts，retry_at，transcript，transcript_origin，analysis，batch_id，error_code。
- podcast_worker_leases：_id 固定 worker 名，owner，expires_at。任务写入可重复；发布 ready 后仍可补发遗漏通知。
- notifications：user_id + episode_id 唯一，title，created_at，read。取消订阅停止后续通知；历史通知仍属于原用户。
- batches：沿用原批次集合，播客来源标为 podcast，附 spark_application_id、spark_master、segment_count、处理统计。

episode analysis 的 segments[] 包含位置、原文、keywords[{term,weight}]、cluster_id 与 similar_segments[{segment_id,score}]；summary 是关键词聚合结果，不虚构自然语言总结。

[方案] podcast_quotas：_id 为 user_id，feed_ids 为最多 20 个共享 feed_id；原子预留订阅名额，避免并发请求越过数量上限。失败请求可用同一 URL 重试或取消释放名额。
