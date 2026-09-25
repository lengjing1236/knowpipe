# Implementation Plan: 规模、评价与关联

**Branch**: `006-scale-evaluation-linking` | **Date**: 2026-09-19 | **Spec**: [spec.md](spec.md)

## Summary
[方案] 先增加冻结JSONL快照和真实规模验收命令，再提供现有排序的盲标包导出/HTML标注/指标导入，最后增加独立Spark关联批次和播客详情延伸阅读。

## Technical Context
[已验证] Python 3.10本机 / 3.11容器，PySpark 4.2、Java、MongoDB 7、Flask、unittest、mongomock。目标Linux本机Web。两个现有库分别保留200篇文献与3集节目；新建 knowpipe_course_006 实验库。
[方案] 文献10k+，单次关联上限20k文献、500片段；只收集最终关联Top3。记录真实耗时、进程树内存抽样和Spark事件日志，不预设性能提升。评价复用现有知识单元排序后按文档去重，不声称全库推荐覆盖率。

## Constitution Check
[已验证] 设计前/后检查通过：核心TF-IDF/余弦/排名使用Spark；Mongo真实存储；Web展示；保留知识单元分类；主源≥10k；人工评价不生成标签；所有计划/证据分开标记。跨源共享词表独立版本不混用旧主题编号。无框架替换或宪法例外。

## Project Structure
- `knowpipe/mining/scale.py`：采集/快照/规模运行与验收。
- `knowpipe/evaluation/study.py`：候选冻结、离线HTML标注、严格导入。
- `knowpipe/linking/job.py`：共享TF-IDF与Mongo发布。
- `knowpipe/web/routes_podcasts.py`、`static/app.js`：授权读取与延伸阅读。
- `tests/mining/test_scale.py`、`tests/evaluation/test_study.py`、`tests/linking/`：失败/真实Spark/授权回归。
- `evidence/006-scale-evaluation-linking/`：计数、时间、人工状态、关联/API验证；原始语料与私有评审留ignored state。

## Execution
[方案] US1→US2工具及候选→US3；US2真实人工指标如缺标签保持待验收。每阶段测试先行，单独提交。现有演示数据库只读复制，不更新个人账号。
