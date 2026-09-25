# 2026-09-25 Spark 执行方式对照

命令：`python3 scripts/compare_spark010.py`。环境为同一台电脑，PySpark 4.2.0 / Java 17 / Python 3.10；本地 `local[2]`，对照为原生 Standalone master + 1 个双核 worker + 2 个单核 512 MiB executor JVM。没有第二台物理主机。

输入是脚本中冻结的 12 篇**合成资料**，用于部署和计算一致性验收；三个目标场景包含 Redis 有/无已读历史及 PostgreSQL 对象约束，不属于独立推荐质量或学习效果评价。

| 项目 | 本地 | 同机 Standalone |
|---|---:|---:|
| 应用总耗时（含启动） | 44.704 秒 | 75.624 秒 |
| 索引构建耗时 | 10.247 秒 | 16.750 秒 |
| 实际 executor JVM PID | 84618（driver） | 86077、86078 |
| 完成任务 | driver 305 | executor 0：145；executor 1：160 |
| 失败任务 | 0 | 0 |

输入摘要、段落产物、特征身份与三个场景的推荐顺序/原文依据一致。向量、分数的最大绝对误差 `5.551115123125783e-17`，小于 `1e-10` 的验收容差。共享路径探针真实执行8个任务，验证双向读写、Python及依赖版本、分词/评分/目标规则/内容模块指纹；所有任务仍位于同一主机 `LAPTOP-2JUIFGTU`。

小数据下 Standalone 更慢，符合它多出进程、调度和通信开销的事实。本结果**不证明多机加速、万条规模下的收益、容灾或学习效果**；跨物理主机验收保持 `pending_resources`。

第一次实际对照失败已保存到相邻目录 `../spark-attempt1-submit-master-failure/`：Python 在 JVM gateway 尚未初始化时读取 SparkConf 得到空配置，导致 `spark-submit --master` 被本地默认值覆盖。验收根据实际 master 和 executor 任务数拒绝通过。修复为初始化 gateway 后读取配置，并补轻量回归，才完成本目录中的第二次对照；没有删除失败记录。

本目录 `comparison.json` 为汇总，`local.json` / `standalone.json` 保存完整运行信息、向量及推荐结果，`.log` 保存原始日志。重型验收结束后仅补充了失败探针的结构化诊断；8条运行时轻量测试通过，包括配置初始化顺序和算法指纹不一致时拒绝运行。

代码指纹对应执行时的冻结快照：当时推荐结果的算法字段仍使用旧 `v3` 标识，但已经运行新的双语/对象规则。之后主分支把选择算法的标识单独改为 `goal-bilingual-object-coverage-mmr-v4`，内容特征仍为 v3；该标识修正不改计算。后续翻译并发保护的改动也不改变 Spark 评分。历史 JSON 与指纹保持原样，不能将其描述为这些后续文件版本已做过完全相同的重型复验。
