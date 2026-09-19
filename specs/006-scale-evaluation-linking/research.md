# Research

- [已验证] 现有StackExchange四站每站最多2500，去重后易少于10k。Decision：增加第五技术站点作为余量，冻结采集结果后重跑；Rationale：避免重复外部请求；Alternatives：API key/全量dump成本更大。
- [已验证] 本地原文献200条，具备两来源各100条切片；Decision：读取既有真实记录作为快照起点，补采真实主源。采集失败和配额耗尽明确报告。
- [已验证] 研究子代理复核两套词表不兼容。Decision：独立联合CV/IDF/Normalizer，用同词项倒排join和Window top3；Rationale：Spark完成相似度，避免全笛卡尔积；Alternatives：直接比较旧簇编号无效，向量数据库增加依赖且偏离核心栈。
- [方案] Decision：关联记录内容指纹、模型run_id与节目batch_id；成功后原子发布指针，读取检查内容版本。空向量不降级随机推荐。
- [方案] Decision：人工评价离线HTML仅含盲标候选，私有manifest保留两种排序，未标注字段为空；Rationale：无需新管理员授权，方便真实评审。Alternatives：模型自动打分不满足人工评价。
