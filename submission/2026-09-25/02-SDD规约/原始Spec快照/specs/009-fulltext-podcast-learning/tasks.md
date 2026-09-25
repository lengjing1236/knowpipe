# Tasks: 多源全文、中文阅读与播客增量推荐
[已验证：本阶段] 19项工程任务已实现并取得运行证据，见[验收记录](../../evidence/009-fulltext-podcast-learning/acceptance.md)。这表示本阶段交付完成，不代表中文检索、教学译文或个性化学习效果已达标。按文件边界并行开发，重型验收串行。

## Phase 1: Setup / Foundations
- [x] T001 建立 specs/009-fulltext-podcast-learning/spec.md、质量复核与功能映射。
- [x] T002 固定 plan.md、data-model.md、contracts/interfaces.md 及代理文件归属。

## Phase 2: US1 全文语料（Agent A）
- [x] T003 [P] [US1] tests/corpus/ 先覆盖完整问答、代码结构、去重、限流和许可溯源。
- [x] T004 [P] [US1] knowpipe/corpus/、recommendations/importer.py、scripts/prepare_fulltext_corpus.py 实现可恢复多源采集与质量报告。
- [x] T005 [US1] 获取并核验 >=10,000 完整计算机文档和至少两个原始提供方，将来源报告写 evidence/009-fulltext-podcast-learning/。

## Phase 3: US2 实际本地模型（Agent B）
- [x] T006 [P] [US2] tests/learning/test_local_providers.py、tests/podcasts/test_audio.py 覆盖完整输出、代码保留、下载限额和失败清理。
- [x] T007 [P] [US2] learning/local_providers.py、podcasts/audio.py、requirements/media.txt 实现配置式本地翻译与ASR。
- [x] T008 [US2] scripts/acceptance_media.py 取得固定模型并用真实多段英文及公开音频推理，保存模型版本/输出/耗时证据。

## Phase 4: US4 推荐和评价（Agent C）
- [x] T009 [P] [US4] 推荐证据测试先覆盖低重合但无共同上下文、过时历史、有效补充等边界。
- [x] T010 [P] [US4] recommendations/engine.py、text.py 增加版本化候选/历史比较和 supplement_eligible，保留透明局限说明。
- [x] T011 [US4] 独立评价脚本在冻结的 CQADupStack queries/qrels 上跑完整候选集与基线，记录指标/无结果率/适用边界。

## Phase 5: US3 增量与业务集成（Root）
- [x] T012 [P] [US3] tests/podcasts/、tests/recommendations/ 先覆盖统一全文发布、通知条件、幂等/租约/版本和增量特征复用。
- [x] T013 [US3] podcasts/worker.py、store.py 接入发布方文字稿/音频转写及统一全文发布，全部更新与个人推荐分开。
- [x] T014 [US3] recommendations/index.py 实现小批增量复用、删除/版本替换和词汇/累计阈值全重建。
- [x] T015 [US3] recommendations/worker.py、queue.py 接入实际翻译、中文就绪及个人/订阅版本约束通知。
- [x] T016 [US3] web 学习页展示RSS状态/个人通知及补充证据；Compose/说明接入worker模型配置。

## Phase 6: Integration / Polish
- [x] T017 使用真实万条语料完成 Spark/Mongo/推荐全链路，保存条数、耗时、作业/阶段与资源证据。
- [x] T018 完成相关回归、RSS到中文推荐集成、浏览器桌面/移动检查；有失败不报完成。
- [x] T019 同步根 plan.md、quickstart.md、验收总结和任务状态，明确本地模型/单机及未完成项。

## Dependencies
T003→T004→T005；T006→T007→T008；T009→T010→T011；T012→T013/T014；T007+T010+T013→T015→T016；T005+T014+T015→T017→T018→T019。
三个代理可同时编写和跑轻量测试；真实模型、万条Spark和外部评价预约串行，避免主机内存争用。Root负责共享文件和最终任务勾选。
