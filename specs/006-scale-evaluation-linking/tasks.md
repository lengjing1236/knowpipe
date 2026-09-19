# Tasks: 规模、评价与关联

## Phase 1: Setup
- [X] T001 建立规约设计与质量检查 specs/006-scale-evaluation-linking/
## Phase 2: Foundation
- [X] T002 检查本机真实数据库、ignored state/及依赖，确定独立实验库；记录 research.md
## Phase 3: US1 规模验收
Independent test：同一冻结输入两次真实运行，主源有效≥10000，文档数一致。
- [X] T003 [US1] 编写快照去重/错误门槛测试 tests/mining/test_scale.py
- [X] T004 [US1] 实现采集快照、真实批次核对、资源统计 knowpipe/mining/scale.py；增加采集余量 knowpipe/mining/collectors/stackexchange.py
- [X] T005 [US1] 执行真实采集和规模重跑，保存 evidence/006-scale-evaluation-linking/scale-*.json
## Phase 4: US2 人工效果评价
Independent test：候选盲标来源隐藏，完整标签才能评价，手算指标一致。
- [X] T006 [US2] 编写标注导入和候选冻结测试 tests/evaluation/test_study.py
- [X] T007 [US2] 实现现有算法候选导出、HTML标注和严格导入 knowpipe/evaluation/study.py
- [X] T008 [US2] 真实数据准备两个学习任务候选 state/course-006/study/，记录 evidence/006-scale-evaluation-linking/evaluation-status.json
- [ ] T009 [US2] 真人完成标签后计算真实效果 evidence/006-scale-evaluation-linking/evaluation-results.json（外部依赖，未完成不得勾选）
## Phase 5: US3 跨来源关联
Independent test：真实Spark相关文献排名、授权隔离、陈旧过滤和失败不发布。
- [X] T010 [US3] 编写Spark关联与API测试 tests/linking/test_job.py
- [X] T011 [US3] 实现联合模型、版本化结果和原子发布 knowpipe/linking/job.py
- [X] T012 [US3] 实现详情API与延伸阅读 knowpipe/web/routes_podcasts.py、knowpipe/web/static/app.js
- [X] T013 [US3] 执行真实关联与浏览器/API验收 evidence/006-scale-evaluation-linking/linking-acceptance.json
## Phase 6: Polish
- [X] T014 完整回归与简明使用说明 README.md、evidence/006-scale-evaluation-linking/acceptance-record.md

## Dependencies / Strategy
[方案] T001→T002→T003→T004→T005→T006→T007→T008。T009依赖真人，不阻止独立的T010→T011→T012→T013→T014。先交付US1，逐故事测试提交。US1采集期间可核对验收格式；US2标注期间可开发US3；US3算法与UI测试准备可并行。实现代码按阶段顺序执行。
