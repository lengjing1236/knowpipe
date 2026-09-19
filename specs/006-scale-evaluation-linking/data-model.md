# Data Model

[方案] Snapshot JSONL：原文献字段；sha256指纹；清洗/去重计数与源计数。ScaleRun：batch_id、snapshot_sha256、code_revision、status、counts、elapsed_seconds、peak_process_tree_rss_bytes、spark_application_id/master、event_log_dir；running→success/failed，不足规模验收accepted=false。

[方案] Study manifest：study_id、k、created_at、corpus_fingerprint、query_id、task、匿名画像快照、personalized/baseline文档ID数组、candidate文献文本；blind HTML只含任务和候选。Judgments：study_id、reviewer、reviewed_at、每query的candidate_id→0..3；禁止缺失/多余ID、布尔评分和重复query。Report：metrics与manifest指纹及人工元数据，缺标签不产生报告。

[方案] Linking run：run_id唯一，algorithm_version、corpus_sha256、参数、Spark信息、输入数、status；全量结果保存完才更新linking_state._id=current指针。Links：(run_id,episode_id,episode_batch_id,segment_id,source,doc_id)唯一；文献/节目内容指纹、score、shared_terms。读取过滤缺失/变化文献和变化节目。无共同词结果为empty；未发布为pending；节目过期为stale。
