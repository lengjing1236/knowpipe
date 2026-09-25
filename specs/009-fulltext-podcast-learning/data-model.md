# Data Model
[方案] 复用文档(source,doc_id)、content.version、processing及translation；正文和推荐证据版本一一对应。
新增可选文档元数据：document_type、source_site、tags、authors、quality、provenance。podcast 文档 doc_id=episode_id，保留feed_id及音频/文字稿URL。播客文本完整进入documents，状态与错误保留podcast_episodes。
补充证据：status、candidate(pid,start,end,text,matched_terms)、comparison(source,doc_id,title,content_version,pid,start,end,text)、overlap、goal_coverage、shared_context_terms、additional_terms、method、limitation；supplement_eligible仅说明词汇比较成立。
索引manifest新增strategy、previous_corpus_id、idf_basis_corpus_id、incremental_generation、changed_documents及rebuild_reason。不兼容算法或缺旧词权重时完整重建。
notifications复用(user_id,episode_id)唯一键，新增kind=learning_recommendation、input_revision、corpus_id、content_version、source/doc_id、reason/evidence；全部更新来自episodes，不能给每个更新自动发个人推荐。

[已验证：实现] 索引增加 `feature_id`，绑定正文快照、IDF基底、词表策略及上一特征签名。相同正文快照在缓存恢复后改用完整重建时，任务身份也改变，旧结果不能继续冒充新权重结果。少量新词以冻结基底中df=0的IDF加入词表；旧向量不变，新词不再静默丢弃。
