# Implementation Plan: 中文演示版与六项交付

**Branch**: `012-demo-delivery` | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

## Summary
[方案] 复用现有Web、MongoDB、推荐worker及Spark管线，建立隔离的中文演示数据与启停工具。新增免费远程翻译提供方。录制实际浏览器流程，生成一套事实一致的提交材料。

## Technical Context
**Language/Version**: Python 3.10，Java 17。
**Primary Dependencies**: Flask、PyMongo、PySpark 4.2、Playwright、python-pptx、python-docx、ffmpeg。
**Storage**: 单独演示MongoDB数据库；私有运行状态放ignored state目录。
**Testing**: 提供方契约测试、演示脚本检查、真实Web流程、视频解码与PPT文件检查。
**Target Platform**: 当前Linux电脑。
**Project Type**: 四层Web系统与课程交付工具。
**Performance Goals**: 预处理在录制前完成；录制控制在300秒内，等待剪辑须明示。
**Constraints**: 不运行本地翻译、不使用收费回退；一次一个重型Spark作业；不改写历史失败。
**Scale/Scope**: 中文小子集演示可操作流程，10215篇背景计算证据单列；不扩大数据集或集群。

## Constitution Check
- I/III：核心检索评分沿用Spark，业务存储沿用MongoDB、实际Web；远程LLM仅辅助翻译。
- II：遵循用户后来确认的“显式已读不等于掌握”，演示真实覆盖证据及不确定，不恢复旧四态掌握标签。
- IV：保留现有万条背景证据。演示使用小子集是截止前录制选择，不冒充完整规模，arXiv不强行加入中文演示。
- V：保留011真实评价和失败结论；012只验收运行与交付，不声称推荐质量合格。
- VI：所有材料区分已验证、方案、待确认。
上述对旧分类/来源的调整来自用户后续明确指令，优先于早期宪法描述。设计后复核相同，未引入新的分布式依赖。

## Project Structure
- `scripts/demo_release.py`：隔离演示准备与启停。
- `knowpipe/learning/`：免费远程适配、工厂与契约测试。
- `scripts/record_delivery012.py`：真实页面录像与证据。
- `scripts/build_delivery012.py`：PPT、目录、校验与打包。
- `submission/2026-09-25/`：六项交付和说明。
- `evidence/012-demo-delivery/`：运行证据，禁止凭据。

## Execution
并行A：演示运行；并行B：远程翻译；并行C：报告与提示词。root负责规格、集成、录像和PPT。录制依赖演示就绪，最终文档依赖实际验收事实，所有提交由root按责任拆分。

## Complexity Tracking
不新增服务框架。沿用既有接口和本机工具，拒绝为录像伪造就绪状态。
