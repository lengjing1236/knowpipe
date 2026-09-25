# Interfaces

[方案] `python -m knowpipe.mining.scale collect --mongo-uri URI --mongo-db DB --snapshot PATH --target 10500`：从DB已有文献快照起步并补采主源，成功输出JSONL，外部原始记录留存于相邻raw.jsonl；限流保留已采记录，不足目标失败退出。
[方案] `python -m knowpipe.mining.scale run --mongo-uri URI --mongo-db DB --snapshot PATH --output PATH`：仅处理快照，记录Spark事件日志/资源统计，真实Mongo核对；默认要求主源10000，补充源≥1，否则非零退出。`--minimum 100`用于小切片，报告门槛明确记录。
[方案] `python -m knowpipe.evaluation.study prepare --mongo-uri URI --mongo-db DB --profiles PATH --output DIR --k 10`：profiles数组含query_id/task/known_topics/known_keywords/read_doc_ids；冻结排名与盲标HTML，标签初始空。
[方案] `python -m knowpipe.evaluation.study evaluate --manifest PATH --labels PATH --output PATH`：严格核对后输出指标；未完成标注非零退出，不伪造效果。
[方案] `python -m knowpipe.linking.job --mongo-uri URI --mongo-db DB --output PATH`：离线关联，失败不发布，限额拒绝静默截断。
[方案] GET `/api/podcasts/episodes/<id>` 保持登录/订阅授权，附加cross_source={status,run_id,items:[{segment_id,source,doc_id,title,source_url,score,shared_terms}]}。未登录401/未订阅404；无共同词empty，尚未构建pending，节目变更stale。前端每段延伸阅读不超3篇，仅使用安全DOM和文献详情接口。
