# Implementation Plan: 学习目标、阅读记录与全文状态基础

**Branch**: `007-learning-foundation` | **Date**: 2026-09-23 | **Spec**: [spec.md](spec.md)
**Input**: specs/007-learning-foundation/spec.md

## Summary

[方案] 以新 /learning 中文工作台交付第一阶段。复用 Flask 会话、CSRF、MongoDB；新 learning 包负责内容契约和个人学习状态。既有 Spark 作业继续保留，本阶段不新增或伪造核心算法结果。

## Technical Context

- **Language/Version**: Python 3.10+、原生 JavaScript。
- **Primary Dependencies**: 现有 Flask、PyMongo、Jinja2；不增加模型依赖。
- **Storage**: MongoDB documents 与 user_profiles；无新事务要求。
- **Testing**: unittest、mongomock，真实 Mongo 可用时补充原子更新检查；Playwright 浏览器冒烟。
- **Target Platform**: 现有 Linux / Docker Web 部署。
- **Project Type**: 四层 Web 应用的业务与数据基础增量。
- **Performance Goals**: 资料每页最多 50 条；分页响应不返回全库正文。详情仅取单篇。
- **Constraints**: 资料正文以 textContent 渲染；所有写操作沿用会话与 CSRF。费用未确认，不触发外部模型请求。
- **Scale/Scope**: 复用现有万条级资料目录，但不声称它们全是有效全文；单一目标和资料级历史。

## Constitution Check

[已验证] 研究前和设计后均按 .specify/memory/constitution.md v1.0.0 检查。

| 原则 | 结论 |
| --- | --- |
| I Spark 核心计算 | 通过：目标存取和历史不是挖掘运算；核心计算继续由 Spark 执行。本阶段不宣称目标已进入评分。 |
| II 知识单元与可追溯 | 用户最新共识优先：资料已读是阅读声明，不推导掌握。保留旧分类实现，但切断读取历史自动派生已知关键词的入口。 |
| III 技术边界 | 通过：复用 Web、MongoDB、Spark；mongomock 仅为测试替身。 |
| IV 规模与来源 | 本阶段不重建语料；总体规模将按用户已确认的跨来源有效全文口径验收，不继续将一万条 Stack Exchange 作为唯一全局来源要求。 |
| V 评价 | 本阶段做状态、隔离与版本验收；算法基线和独立评价仍为后续必要工作。 |
| VI 文档状态 | 通过：方案、已验证及待确认分别记录，完成任务必须有验证证据。 |

[已验证／偏离依据] 根目录 plan.md 的 B1/C1/B2/B3/E1/E2 是用户新决定；开发者要求用户指令优先于本地规则。旧宪法与 001–006 文档作为历史基线保留；本功能不修改旧实现规格，也不把尚未重建的语料声称为已合规。后续整体规约需同步宪法的知识定义和来源限定。

## Project Structure

```text
specs/007-learning-foundation/
  spec.md plan.md research.md data-model.md quickstart.md tasks.md
  checklists/requirements.md
  contracts/api.md
knowpipe/learning/
  __init__.py content.py providers.py store.py
knowpipe/web/
  routes_learning.py app.py routes_pages.py routes_api.py mongo_sink.py
  templates/learning.html templates/index.html
  static/learning.js static/style.css
 tests/learning/
  test_content.py test_store.py test_routes.py
```

[方案] 使用 learning 包划清业务/数据职责；不引入消息中间件或新数据库。用户画像同一文档内原子更新目标、历史和修订号，避免跨集合双写。内容和翻译绑定全文版本，处理结果用条件写入阻止过期任务覆盖。

## Complexity Tracking

[方案] 没有新增服务和依赖。新增工作台独立于旧关键词页面，首页提供入口；历史兼容旧 read_doc_ids 字段。阶段边界见 spec.md Assumptions；已确认的推荐、RSS、全文补采工作不能因本阶段完成而被取消。
