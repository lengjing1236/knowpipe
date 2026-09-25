# Tasks: 目标与历史推荐

[已验证] 本阶段 15 项任务完成，证据见 [验收记录](../../evidence/008-goal-history-recommendations/acceptance.md)；总体未完要求仍见根规划。

## Phase 1: Setup and foundation

- [X] T001 建立 knowpipe/recommendations/ 与 tests/recommendations/，核验新规约及已通过的 checklists/requirements.md
- [X] T002 编写 tests/recommendations/test_text.py，实现在 knowpipe/recommendations/text.py 的版本化分词与原文段落偏移
- [X] T003 在 knowpipe/recommendations/index.py 实现流式全文快照、指纹与 Spark 稀疏特征缓存

## Phase 2: US1 目标召回

- [X] T004 [US1] 编写 tests/recommendations/test_engine.py 的真实 Spark 基线与空/OOV/仅标题测试
- [X] T005 [US1] 在 knowpipe/recommendations/engine.py 实现正文目标召回、来源与片段证据
- [X] T006 [US1] 在 knowpipe/recommendations/importer.py 实现带来源声明的全文 JSONL 导入

## Phase 3: US2 历史与去重

- [X] T007 [US2] 在 tests/recommendations/test_engine.py 添加历史版本、重复镜像、无关新词及引用一致性测试
- [X] T008 [US2] 在 knowpipe/recommendations/engine.py 实现 Spark 历史覆盖惩罚、有界 MMR、基线与消融输出

## Phase 4: US3 异步任务与页面

- [X] T009 [US3] 在 tests/recommendations/test_queue.py 验证去重、抢占、过期、隔离与有限重试
- [X] T010 [US3] 在 knowpipe/recommendations/queue.py、worker.py 实现持久队列、单活 worker、复用 Spark、版本保护及入选后中文准备
- [X] T011 [US3] 接入 knowpipe/web/routes_learning.py、app.py 的推荐接口，在 tests/recommendations/test_routes.py 验证会话与 CSRF
- [X] T012 [US3] 更新 knowpipe/web/templates/learning.html、static/learning.js 展示推荐状态、依据与阅读入口
- [X] T013 [US3] 更新 compose.yaml、Dockerfile 启动独立推荐 worker 并持久化快照目录

## Phase 5: 验收与同步

- [X] T014 用 scripts/acceptance_recommendations.py 验证真实 Spark/Mongo/浏览器链路，记录 evidence/008-goal-history-recommendations/acceptance.md；实际中文来源样例与测试样例分开
- [X] T015 同步 plan.md、specs/008-goal-history-recommendations/quickstart.md 和本任务清单，保留整体未完要求

## Dependencies and execution

[方案] T001→T002→T003→T004/005→T007/008；T006 可独立进行。算法就绪后 T009/010→T011/012/013→T014→T015。共享文件顺序编辑；先测试关键行为再实现。独立研究已完成，不为并行拆分依赖编辑。
