# Implementation Plan: 中文目标匹配与计算部署准备
**Branch**: `010-quality-cluster-readiness` | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

## Summary
[方案] 执行总体plan的阶段A：双语目标召回、显式技术对象约束、译文版本/质量基础，以及只有一台机器时可证明的集群准备。更强模型、主题关系视图和任务匹配的学习效果评价列为后续阶段，不冒充本轮已完成。

## Technical Context
Python3.10、Flask、Mongo7、PySpark4.2/Java17、CTranslate2/SentencePiece。已有英中Argos模型；新增固定版本的中英Argos模型作目标转换。本机约7GiB内存，仅3.6GiB可用；无Docker、GPU或第二台主机。重型模型/Spark运行串行。采用同机Standalone实际独立executor验收，而非local-cluster模拟。目标至多1000字符，译文正文仍4,000,000字节，核心候选100、历史2000保持有界。

## Constitution Check
[已验证] 保留Spark核心评分、Mongo存查管、真实Web与原文证据；模型只转换目标/中文阅读，不直接产出推荐分数。
[显式偏离] 延续用户已确认的007–009新方向：不恢复known/new掌握分类，不强制StackExchange+arXiv来源；万条多源全文要求保留。宪法旧II/IV与当前用户决定冲突，以用户决定优先。历史规约保留。
[复杂度选择] 不引入向量数据库或Kafka。先做可解释双语词汇召回与对象约束，保留后续语义基线比较；用共享挂载作为当前文件管道最小多机准备，不虚构HDFS已接入。

## Research Decisions
见[research.md](research.md)。已确认只有单机，跨主机验收转为资源依赖，设计本身没有未解决的实现阻塞。

## Project Structure / Ownership
- Root：recommendations/query.py、engine.py、queue.py、worker.py、Web、SDD与最终集成。
- Media agent：learning/local_providers.py、providers.py、content.py、新quality模块、媒体测试/真实目标模型获取。
- Spark agent：recommendations/runtime.py、部署脚本/模板、独立执行器验收及本域测试。
不同文件并行，共享契约先冻结；不并发运行Spark/模型重型验收，不操作Git提交。

## Design
1. 目标处理层调用实际zh→en提供方；Mongo缓存按原目标和处理器身份建立。保留原目标和转换状态。纯英文不必转换。
2. Spark分别为原目标与转换目标构造向量、按各自词项数做覆盖门槛；同段取更相关分支，不把两种语言拼成更苛刻的覆盖分母。历史采用同样召回。
3. 识别注册技术名称/别名与明确反引号名称；document正文或标题须满足所指定对象，展示约束。初版每个显式对象均需满足；这是可解释规则，不声称自动理解全部实体。
4. 目标解释版本、实体规则和模型处理器ID进入任务身份；模型变动触发重算，内容特征版本与任务版本分开。
5. 翻译记录processor_id和质量报告，正文及预期处理器一起CAS。检测数字/百分比及代码丢失，缺陷不发布；检查不是语义质量保证。
6. 外置Spark配置尊重spark-submit；集群模式要求共享目录确认和driver连通参数。分发代码，验证executor能读同一快照；记录真实执行器及任务数。

## Validation
先轻量失败/版本/双语与对象测试，再真实模型8组目标（4领域）和真实公开资料检索，最后本机Standalone多executor对照。新的场景标作开发验收，不再把已观察的009测试集当未见测试。保留没有结果与错误，不调查询来掩盖问题。浏览器核验解释与质量状态，旧用户隔离/RSS/版本回归。

## Complexity Tracking
[已验证：设计复核] 新增目标解释缓存是跨语言入口所需；新增处理器身份防止缓存混用；共享挂载是现有Path管道的显式部署约束。没有引入与当前目标无关的服务。
