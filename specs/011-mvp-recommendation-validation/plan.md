# Feature 011 实施计划

日期：2026-09-25；关联根目录 plan.md M1–M4。状态：[方案] 实施中。

## 技术上下文与边界

沿用 Python 3.10、Flask、MongoDB、PySpark 4.2、Java 17、CTranslate2／ONNX Runtime，单机 CPU、约 7 GiB 内存。现有约 10,215 篇全文与版本索引复用；不同时运行 Spark 大任务与模型评估。开发、轻量单测、案例编制并行；重型计算串行。

## 四层职责

1. Web：中文目标、显式已读、原文／中文阅读、双侧证据及比较不足状态。
2. 业务：worker 租约、排队、处理版本、自动翻译、RSS 通知门控。
3. 数据管理与挖掘：Mongo 原文／历史／缓存；语义特征产生、对照范围与自定义选择规则。
4. Spark：全库词汇候选召回、版本历史筛选、语义特征关联、句级覆盖聚合和最终选择。模型提供特征，不直接代替推荐列表。

## 选择与依据

[方案] 保留现有全库 TF-IDF 索引，用宽召回后有界相关性重排、句子相似性匹配、方向性蕴含比较修复词汇缺口。仅候选段进行模型推理，不构建全库大模型向量。相关性先门控；正文证据必须具有说明内容；历史能支持候选陈述时计覆盖，矛盾／无法比较计不确定，不能把 neutral 等同已证实新知识。

[方案] 最多 100 文档×3 段重排、90 候选句、100 历史句、270 比较对；超过输入窗口拒绝／分句，记录范围。相关性与向量改用多语模型直接处理原中文（初步NLLB实测失败后决策）；英文NLI遇中文必须用真实翻译且保留原文位置。任何语义缺失／失败禁用候选补充通知，允许带状态的目标词汇基线。

[方案] 候选模型：官方量化 MiniLM rank/embed/NLI（约 130 MB）及 NLLB 600M CT2 int8（约 579 MB）。后者对照现有 Argos 后决定是否默认使用；必须实测中文含义。模型下载受限，记录来源、许可证、SHA，使用本地 state/ 不入 Git。许可及质量局限见 research.md。

## 并行划分与依赖

- 根代理：先冻结 Spec／接口／任务，然后负责 query／worker／queue、RSS、Web、真实浏览器闭环和验收报告。
- 算法代理：engine.py、semantic*.py、算法测试、模型准备脚本。
- 语言代理：learning/providers.py、local_providers.py、content.py、quality.py、语言测试／模型脚本。
- 评估代理：真实原文事实、冻结案例、评价程序和基线／新算法报告；不修改算法，不把看到的新结果反向写进预期。

顺序：M1 规格及接口 → M2 案例冻结（生产算法编辑前）→ M3 三路并行 → 根代理集成 → 串行真实模型／Spark 验证 → M4 真 Web 和增量通知。语言与算法先分别用契约测试，集成不得修改另一代理文件而不协调。

## 文件布局

`specs/011-mvp-recommendation-validation/{spec,plan,research,data-model,tasks,quickstart}.md`、`contracts/runtime.md`；`knowpipe/recommendations/semantic*.py`；`scripts/prepare_semantic011.py`、`scripts/prepare_language011.py`；`scripts/evaluate_mvp011.py`、`scripts/acceptance_mvp011.py`；`evidence/011-mvp-recommendation-validation/`。

## 宪法核对

[已验证] 保留真实 Spark、MongoDB、Web、可追溯性与基线。按用户后续明确决策覆盖旧宪法中的两个假设：不再以关键词簇宣称 known/new 掌握程度；不强制 arXiv 摘要凑来源。真实全文与主动已读是现行需求。当前先正确性后规模，已有万条基线仍保留；多主机集群不是此次验收前提。神经模型只计算相关／蕴含特征，自主 Spark 选择规则及消融保留；复杂度只针对已验证的 G1／G3／G4 缺口。

## 完成规则

工程测试通过与 SC 效果通过分别记录。真实模型产生错误时修复有因果依据的实现问题；保留集一经观察即不再用于调参后声称盲测。不能通过降低门槛、硬编码测试答案、人工替换翻译、注入 job 结果宣称 MVP 完成。
