# 数据模型与版本

[方案] recommendation_runtime 单文档保存单活 worker 令牌、lease_expires_at、heartbeat_at、当前 corpus_id、generation 和语料统计。所有续租与发布都以有效令牌为条件。

[方案] 公共快照按 `(source,doc_id,content_version,title,source_url)` 和算法版本生成哈希。JSONL 流式写入后由 Spark 建 Parquet：paragraphs 含原文 start/end/text、doc_key、version；postings 含 pid/term/归一化 weight；terms 含 idf。完整性需逐篇核验，文件缓存只在指纹相同时复用。

[方案] recommendation_jobs 使用确定性 job_id（用户+revision+corpus+算法参数），保存 goal/history 输入快照、status、attempt_token、lease_expires_at、attempts、结果和错误代码。唯一 job_id；status/lease 索引用于恢复。最多三次尝试。

[方案] user_profiles 保存 desired_recommendation_job 和推荐语料 generation；同一 revision 下仅允许更高或相同 generation 替换目标任务。完成只更新任务文档，GET 用当前 profile 的 desired 指针读取并核对账户、revision 和 corpus；避免旧任务覆盖新指针。

[方案] 结果保存推荐条目、基线/消融顺序、候选与有效历史版本清单、片段偏移、分数与原文依据。所有依据来源均核验版本；来源改变返回 stale，不将旧解释显示为当前。

[方案] 状态：needs_goal / waiting_for_worker / waiting_for_corpus / queued / running / ready / empty / stale / failed。empty 原因区分无合格全文、目标无词项/无匹配、重复过滤后不足。历史不可比较数单独返回。

[已验证／存储实现] 每次索引构建写入独立不可变目录，完成后原子替换 index.json 指针；旧 worker 不覆盖新任务正在读取的 Parquet。文档上的 recommendation_analysis 记录 corpus_id/content_version，用于核验“分析已就绪”；不覆盖旧 mining 批次。JSONL 导入保留许可和来源声明，重复同正文不会重置成功译文。
