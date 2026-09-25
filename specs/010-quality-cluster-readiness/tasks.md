# Tasks: 中文目标匹配与计算部署准备

本文件只执行总体 plan.md 的阶段 A；阶段 B/C/D 保持未完成。基于一台物理电脑；同机独立执行进程不代表跨主机。任务标记表示实现与其验证完成，不能替代整体效果验收。

## Phase 1 — 规约与基线
- [X] T001 归档旧计划至 docs/plan-before-feature010-2026-09-25.md，完成根 plan.md 差距、步骤与分布式影响说明。
- [X] T002 完成 specs/010-quality-cluster-readiness/ 下 spec.md、plan.md、research.md、data-model.md、contracts/interfaces.md 和需求质量检查。
- [X] T003 固定 evidence/010-quality-cluster-readiness/goal-cases.json 的八组双语开发目标，覆盖至少四个方向；声明非独立效果评测。

## Phase 2 — 基础接口（允许按文件并行）
- [X] T004 [P] 在 tests/learning/ 增加处理器升级、数字/代码完整性与反向翻译契约测试，验证失败后实现。
- [X] T005 [P] 在 tests/recommendations/test_query.py 增加中文解释缓存、失败回退、实体别名与多对象测试，验证失败后实现。
- [X] T006 [P] 在 tests/recommendations/test_runtime.py 增加 Spark 配置优先级及共享路径前置条件测试，验证失败后实现。

## Phase 3 — US1 中文目标
- [X] T007 [P] [US1] 扩展 knowpipe/learning/local_providers.py 的模型方向及 configured_goal_translator，提供确定性处理器身份；获取有界免费模型并记录来源指纹。
- [X] T008 [US1] 新增 knowpipe/recommendations/query.py，保留原目标、缓存成功解释、显示转换失败及版本状态。
- [X] T009 [US1] 修改 knowpipe/recommendations/engine.py，分别归一化原文/译文召回，再按片段合并；保留中文来源。
- [X] T010 [US1] 修改 knowpipe/recommendations/queue.py、worker.py，任务身份及重试预算绑定处理器，版本改变使旧结果失效。

## Phase 4 — US2 技术对象
- [X] T011 [US2] 在 query.py、engine.py 实现技术名称/别名约束、正文或标题证据与候选不足说明。
- [X] T012 [US2] 在 tests/recommendations/ 验证泛词干扰、别名、多对象、双语召回和历史兼容性。

## Phase 5 — US3 译文基础质量
- [X] T013 [P] [US3] 修改 knowpipe/learning/providers.py、content.py 并新增 quality.py，实现译文处理器绑定、版本 CAS、数字/代码缺失门禁与有限风险说明。
- [X] T014 [US3] 修改 knowpipe/web/static/learning.js，展示查找解释、对象约束、中文质量状态；既有全文、已读与 RSS 行为保持兼容。
- [X] T015 [US3] 运行真实英文全文翻译并保存处理器及检查结果至 evidence/010-quality-cluster-readiness/，不把规则通过称作语义准确。

## Phase 6 — US4 计算准备
- [X] T016 [P] [US4] 新增 knowpipe/recommendations/runtime.py 及 scripts/ 下部署预检查与提交工具，尊重 spark-submit，验证共享路径和工作进程依赖。
- [X] T017 [US4] worker.py 接入运行时；固定输入对照本地与同机 Standalone 独立执行进程，记录实际 executor、产物、排序与耗时。
- [X] T018 [US4] 在 docs/ 与 quickstart.md 写出单机可执行方式及未来多主机必要条件，明确当前不具备跨主机验收资源。

## Phase 7 — 集成与记录
- [X] T019 运行八组真实中文目标转换及现有一万条全文索引检索，保留逐项结果和不足；不重写既有 009 原始证据。
- [X] T020 完成相关回归测试与中文页面检查；记录实际命令、失败、修复和测试范围。
- [X] T021 更新 evidence/010-quality-cluster-readiness/acceptance.md、根 plan.md 及本 tasks.md，核对规约与实现，列出后续 B/C/D 未完成项。

## 依赖与并行边界
T001–T003 先行；T004/T005/T006 可并行。媒体负责人独占 local_providers/providers/content/quality 与媒体测试；计算负责人独占 runtime、部署/预检查脚本及其测试；主代理负责 query/engine/queue/worker/Web、集成和任务状态。T010 依赖 T007/T008/T013 的接口；T014 依赖 T009/T011/T013；T017 依赖 T016。重型 Spark 与模型验收在本机串行执行，避免内存互相挤占。所有阶段 A 验收后才可标记 T021，不把后续总体路线图提前勾选。
