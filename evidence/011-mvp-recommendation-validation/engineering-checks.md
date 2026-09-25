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

## 后续修复回归

- 合并上下文门控、翻译模式隔离及第一批验收器修复后，同一轻量命令运行 **214 passed in 18.41s**，原始输出见 `final-light-regression.log`。这是该时间点的回归，不包含随后新增的三项评价器测试。
- 随后评价器补齐“合成材料不能抵充真实正例／冻结负例不能被审核标签覆盖／失败空结果不能算成功拒绝”，评价与回放判定共 **18 项通过**。与214项存在重叠，不相加。
- 最新算法专项为19项轻量测试、2项真实Spark集成；详情及代码SHA见 `v6-algorithm-tests.json`。PG实际上下文诊断见 `context-diagnostic-results.json`，不能以组件改善替代最终推荐效果。
- 浏览器与真实RSS脚本新增语义处理状态断言；选中播客必须准备中文并产生当前有效通知；ASR异常由创建方清理临时数据库。编译及JavaScript语法检查通过，实际流程结果另记。

- 浏览器真实执行后发现验收器将一次失败当成最终失败，现已区分可重试、运行中、稳定终态及超时，并保留失败页面与逐篇版本/次数/质量记录。11项新测试与5项既有重放测试共16项通过，见 `browser-harness-tests.log`；这是验收器回归，不算真实阅读通过。

- 语义会话生命周期修复由算法代理执行 `python3 -m pytest tests/recommendations/test_worker.py tests/recommendations/test_semantic.py tests/recommendations/test_integration011.py -q`，21项通过（3.02秒）。覆盖先发布结果、释放会话、再翻译，同一provider下一作业惰性重建，以及清理异常不覆盖原结果。此为代理执行记录，不声称已测得具体内存节省；新进程Web复验另记。
