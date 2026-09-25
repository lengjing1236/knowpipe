# Implementation Plan: 目标与阅读历史推荐

**Branch**: `008-goal-history-recommendations` | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

## Summary

[方案] MongoDB 流式导出合格全文，Spark 建立可复用段落 TF-IDF 倒排快照。目标召回、历史段落重合与候选相似度在 Spark 分区执行；有界候选的覆盖惩罚和 MMR 选择也在 Spark 任务内计算。独立 worker 轮询持久任务，Web 读取结果。

## Technical Context

- Language: Python 3.10+，原生 JS；沿用 PySpark 4.2、MongoDB、Flask、jieba。
- Storage: 原集合加 recommendation_jobs、recommendation_runtime；本机共享 Parquet 快照缓存。
- Testing: unittest/mongomock、真实 Spark local[2]、真实 Mongo 租约检查、Playwright。
- Performance: 默认每次最多 100 个候选、每篇 3 个目标相关片段、最多 10 条结果；只收集有界标量和最终证据，不 collect 全库向量。
- Constraints: 显式上限 200,000 段落、2,000 条历史；超限失败。当前本地文件快照用于 local 模式，跨主机需可共享存储，不声称已分布式部署。
- Scope: 自动目标推荐、历史对照、去重、真实样例验收；不实现完整 RSS/ASR 或未知费用的服务。

## Constitution Check

[已验证] 研究前及设计后对照旧宪法：Spark 运算、MongoDB/Web、不伪造证据及基线评价均保留。用户已确认的资料级已读与跨来源总体规模优先于旧宪法的关键词知识标签及固定 Stack Exchange 条数；Feature 007 已记录这一变更依据。本阶段不把资料重合推导为掌握，也不修改旧功能历史规约。该偏离由用户新指令支持，不是静默忽略。

## Project Structure

```text
knowpipe/recommendations/{text,index,engine,queue,worker,importer}.py
knowpipe/web/routes_learning.py
knowpipe/web/static/learning.js
knowpipe/web/templates/learning.html
tests/recommendations/
scripts/acceptance_recommendations.py
specs/008-goal-history-recommendations/{research,data-model,quickstart,tasks}.md
specs/008-goal-history-recommendations/contracts/api.md
```

## Complexity Tracking

[方案] 复用现有 worker 镜像，新增独立推荐 worker 服务及缓存卷，不引入 Kafka、向量库或额外模型。TF-IDF 是标准基线；自主实现为目标相关段落的历史覆盖惩罚与 MMR 适配，不宣称发明新算法。版本化参数预先固定，机制样例不作为调参后自证效果。

[已验证／执行结果] 15 项任务完成，真实中文全文、Spark/Mongo/浏览器及 77 项测试记录见 [验收](../../evidence/008-goal-history-recommendations/acceptance.md)。本机集成已验证，Docker 仅检查配置结构；万条、模型、RSS 和独立效果验收不属于本次已完成声明。
