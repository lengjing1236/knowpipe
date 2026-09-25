# 011 工程验证记录

[已验证] 2026-09-25。这些检查不证明相关性、补充价值或中文教学质量通过。后续改动需要相应增量回归。

- 集成新门控测试先出现3项失败：语义身份未进入缓存、旧词汇标记可发播客补充通知、语义失败不能主动重试；修改后通过。
- 新增原文／中文位置关联测试最初因模块不存在失败；实现后验证版本变化、伪造片段和旧无对齐译文均不能生成虚构中文证据。
- 新增worker生命周期测试：真实推荐结果先发布，全文翻译阶段使用worker租约和当前版本保护，完成job不会中断翻译所有权。
- 原文来源身份测试：官方适配器类型和精确HTTPS域名共同作为技术对象证据，拒绝伪装域名／其它来源引用链接。
- 轻量回归：188 passed in 27.14s。命令：

```bash
python3 -m pytest tests/learning tests/corpus tests/recommendations tests/podcasts --ignore=tests/recommendations/test_engine.py --ignore=tests/recommendations/test_engine_semantic.py --ignore=tests/recommendations/test_incremental.py --ignore=tests/podcasts/test_analysis.py -q
node --check knowpipe/web/static/learning.js
```

真实Spark／模型／Web闭环另记；没有将它们包含在188项通过数中。
