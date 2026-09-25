# Tasks: 学习目标、阅读记录与全文状态基础

**Input**: spec.md、plan.md、research.md、data-model.md、contracts/api.md
**Tests**: [方案] spec 要求对身份隔离、幂等并发、内容版本及页面做验证；先写关键行为测试再实现。

## Phase 1: Setup

- [x] T001 核验规约与忽略规则并建立包 knowpipe/learning/__init__.py、tests/learning/__init__.py

## Phase 2: Foundational

- [x] T002 编写内容完整性读取测试 tests/learning/test_content.py，并在 knowpipe/learning/content.py 实现保守的旧数据视图与版本指纹
- [x] T003 在 knowpipe/web/app.py 注册 knowpipe/web/routes_learning.py，复用登录与 CSRF；在 knowpipe/web/mongo_sink.py 修正画像创建并发冲突

## Phase 3: US1 保存目标

**独立验收**: 保存、刷新、两用户隔离、相同目标幂等、非法输入拒绝。

- [x] T004 [US1] 在 tests/learning/test_routes.py 编写目标接口及会话/CSRF测试
- [x] T005 [US1] 在 knowpipe/learning/store.py、knowpipe/web/routes_learning.py 实现单一目标与输入修订号

## Phase 4: US2 阅读历史

**独立验收**: 浏览不标记、同源同 ID 去重、不同来源独立、撤销与版本变化、并发不丢失。

- [x] T006 [US2] 在 tests/learning/test_store.py、tests/learning/test_routes.py 编写历史幂等、隔离与旧评分兼容测试
- [x] T007 [US2] 在 knowpipe/learning/store.py 实现原子标记/撤销及带版本信息的分页历史
- [x] T008 [US2] 在 knowpipe/web/routes_learning.py 接入 read-state/history，支持删除后撤销、斜线 ID 和输入校验
- [x] T009 [US2] 在 knowpipe/web/routes_api.py 切断已读自动派生已知关键词路径，并在 tests/learning/test_routes.py 验证

## Phase 5: US3 全文及处理状态

**独立验收**: 七类内容样例状态准确，过期任务不可覆盖新版本，无服务不伪造成功。

- [x] T010 [US3] 在 tests/learning/test_content.py 编写全文版本、缓存、部分译文、过期写入及提供方失败测试
- [x] T011 [US3] 在 knowpipe/learning/content.py、knowpipe/learning/providers.py 实现版本化全文发布、处理状态、模型接口与翻译结果保护
- [x] T012 [US3] 在 knowpipe/learning/store.py、knowpipe/web/routes_learning.py 实现分页资料库、标题筛选和内容详情

## Phase 6: Web 集成与验收

- [x] T013 集成三条路径至 knowpipe/web/templates/learning.html、knowpipe/web/static/learning.js，并在 knowpipe/web/routes_pages.py、knowpipe/web/templates/index.html 提供入口，必要样式加入 knowpipe/web/static/style.css
- [x] T014 运行 tests/learning 与受影响回归，通过 scripts/acceptance_learning.py 完成真实 Mongo 与桌面/手机浏览器检查，记录 evidence/007-learning-foundation/acceptance.md
- [x] T015 同步 plan.md、specs/007-learning-foundation/tasks.md 与 quickstart.md，区分阶段完成和后续未完成要求

## Dependencies & Parallel Opportunities

[方案] T001 → T002/T003 → US1 → US2 → US3 → T013 → T014 → T015。US1/US2 共享 store/routes 文件，顺序实施；US3 的提供方协议可独立研究，但本轮不为并行而拆分编辑。每个故事均以接口行为独立验收；UI 在共同集成阶段检查。

## Implementation Strategy

[方案] 完成本文件所有任务；第一阶段不代表全部根规划实现。下一阶段需补齐真实全文采集、目标与历史参与的 Spark 推荐、RSS 自动转写、翻译服务及其资源/效果验收。没有真实证据不勾选相关总体成果。

[已验证] 15/15 任务完成；25 项新增测试、59 项原 Web 回归、8 项原播客链路回归通过。真实 Mongo 并发和桌面/手机页面验收通过，见 evidence/007-learning-foundation/acceptance.md。
