# Feature 011 tasks

状态：[方案] 按根计划 M1–M4；并行已获用户明确授权。文档勾选表示该工程动作完成，SC 另行判定，不等于 MVP 通过。

## Phase 1：M1 规格与接口

- [X] T001 冻结用户故事／FR／SC 与宪法偏离，写 specs/011-mvp-recommendation-validation/spec.md、plan.md、research.md。
- [X] T002 冻结并行文件职责与语言／语义／worker 契约，写 specs/011-mvp-recommendation-validation/contracts/runtime.md、data-model.md。

## Phase 2：M2 独立预期（生产算法编辑前）

- [X] T003 [P] 根据真实原文冻结四主题场景、负例、保留集及来源偏移，写 evidence/011-mvp-recommendation-validation/cases.json 与 docs/mvp-evaluation-design-2026-09-25.md。
- [X] T004 [P] 建立有界模型下载、身份与契约测试，写 scripts/prepare_semantic011.py、scripts/prepare_language011.py 及 tests/recommendations/test_semantic.py、tests/learning/test_language011.py。

## Phase 3：M3 三路开发

- [X] T005 [P] [US1] 先测试正文相关性／标题／错误对象／模型缺失，再实现有界语义 provider 与召回重排，写 knowpipe/recommendations/semantic.py、engine.py 及对应 tests/recommendations/。
- [X] T006 [US2] 测试改写、部分补充、不可比较，令句级覆盖与补充实际参与 Spark 选择，保留消融与双侧跨度，写 knowpipe/recommendations/engine.py、semantic.py。
- [ ] T007 [P] [US3] 测试全文对齐、数字与标识符损坏、版本隔离，再实现真实双向候选翻译及段落对齐，写 knowpipe/learning/providers.py、local_providers.py、quality.py、content.py。
- [X] T008 [P] [US1] 建立不依赖实现分数的真实案例评价程序与旧基线记录，写 scripts/evaluate_mvp011.py 和 evidence/011-mvp-recommendation-validation/。
- [X] T009 [US4] 集成处理身份、worker 调用及 RSS 安全降级，写 knowpipe/recommendations/query.py、worker.py、queue.py、knowpipe/podcasts/learning.py 和对应回归测试。
- [ ] T010 [US3] Web 展示真实比较范围、双侧中文／原文与段落对齐、失败状态，写 knowpipe/web/static/learning.js 和必要样式。

## Phase 4：M4 串行真实验证

- [ ] T011 [US3] 实际候选模型与 Argos A/B，逐条核对冻结技术事实、完整英文全文，写 evidence/011-mvp-recommendation-validation/language-quality.json。
- [ ] T012 [US1] 小真实集和万条背景运行基线／新算法／消融／保留集，按 SC 记录正确、错误、待判定，写 evidence/011-mvp-recommendation-validation/evaluation.json。
- [ ] T013 [US4] 编写并执行真实浏览器→worker→Spark→翻译→已读重算闭环与新增三分支，写 scripts/acceptance_mvp011.py 和 evidence/011-mvp-recommendation-validation/browser.json。
- [ ] T014 运行针对性回归，复核 spec→code→evidence，更新 evidence/011-mvp-recommendation-validation/acceptance.md、根 plan.md、README.md、quickstart.md。

## 依赖与并行

T001–002先完成。T003 与 T004 并行；T003冻结后 T005–008可并行，其中T006依赖T005，T005/006同一代理，T007语言代理，T008评估代理。根代理准备T009/010测试及闭环脚本，接口就绪后集成。T011/T012/T013重型部分串行，T014最后执行。禁止两个代理修改同一文件；需要跨域改动由根代理协调。
