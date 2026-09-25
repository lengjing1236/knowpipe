# Implementation Plan: 多源全文与播客增量推荐
**Branch**: `009-fulltext-podcast-learning` | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

## Summary
[方案] 在 Feature 007 版本化正文/已读记录和 Feature 008 Spark 推荐上增加真实全文采集、本地模型、可追溯补充证据、RSS统一入库与增量通知。三个代理独占文件并行，主代理集成。

## Technical Context
Python 3.10、Flask、MongoDB 7、PySpark、Java 17；本地 CTranslate2 英中翻译、faster-whisper CPU int8 转写，ffprobe 校验音频。约 7 GiB 主机内存，重型模型和 Spark 验收串行。数据/模型缓存置 ignored state/；不接触已有用户数据库和凭据。

## Constitution Check
[已验证] I/III/V/VI：Spark 执行词权重、相似度与推荐；Mongo 存查管；真实 Web；基线与外部标签评价；事实与方案分开。
[方案：显式偏离] II 的 known/new 分类被用户确认的显式已读片段对照代替，不能推断掌握或未知；IV 固定 SE+arXiv 来源被广泛 CS 多源全文替代，万条要求保留。延续 007/008 的理由，不修改历史证据。

## Project Structure and Ownership
- Agent A：knowpipe/corpus/**、recommendations/importer.py、tests/corpus/**、scripts/prepare_fulltext_corpus.py、来源证据。
- Agent B：learning/local_providers.py、podcasts/audio.py、对应新测试、requirements/media.txt、scripts/acceptance_media.py、媒体文档证据。
- Agent C：recommendations/engine.py、text.py、新 evaluation 模块/脚本/测试、评价证据。
- Root：recommendations/index.py/queue.py/worker.py、RSS worker/store/API、Web、Compose、SDD、集成测试与总验收。
共享协议修改须先协调，代理不提交 Git，不覆盖前序未提交改动。

## Four Layers
Web 展示推荐、中文/原文、比较片段、RSS状态和通知。业务层控制目标/已读修订、任务租约、版本和订阅条件。数据/挖掘层定义统一全文、来源质量、段落特征、评分和评测。Spark 执行批量/增量索引、召回、历史相似与排序。本地翻译/ASR是辅助服务。

## Incremental Strategy
[已实现] 小批变更冻结旧词 IDF，仅对新增或变更正文切段与向量化，保留不变文档特征、剔除旧版本/删除记录。少量新词按冻结基底中的零文档频次扩展权重，保证可检索；新增词比例超过 25%、单次变更超过 15%、累计超过 30% 或达到 8 代时全重建。记录 basis corpus、代数及 feature_id，避免特征基底不同却复用旧任务。Mongo 仍扫描快照，合并 Parquet 仍涉及全量文件读写；增量指复用特征，不声称完全避免全量 I/O。

[已验证] 10,215 篇正文切分为 222,579 个可分析段落，使用 15 个输入分区、16 个 shuffle 分区与磁盘持久化完成建索引。最初单分区/512 MiB 运行内存不足，修复后以 768 MiB、local[1] 成功；失败证据保留。真实播客追加时只重算 1 篇，复用 10,215 篇旧文档特征。详见阶段验收。

## Execution / Validation
先冻结契约和质量复核，再三路并行，主代理先实现 RSS 与增量骨架；轻量测试可并行，真实 Spark 与模型验收预约串行。最后真实万条数据、模型、独立评测、Mongo/Web全链路及桌面/移动页面验证。无法取得足够语料/模型时相关任务保持未完成并记录真实原因，不降低验收口径。
