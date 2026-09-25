# 最终集成后的同机 Spark 对照

本目录是最终 v4 排序标识、翻译generation保护及16MiB默认读取分区合并后的重跑。每个报告包含实际代码指纹，首轮master覆盖失败与较早成功记录分别保存在旁边的 `spark-attempt1-submit-master-failure/`、`spark-attempt2-before-final-integration/`，没有回写历史。

12篇明确合成资料、相同词汇特征与3组有/无历史目标；本地local[2]与原生Standalone一个双核worker、两个独立executor JVM比较。物理电脑数量为1，不是跨主机或万条规模性能验收。

- local 71.091秒，Standalone 107.217秒（包括启动）；索引18.545/20.097秒。
- executor 1/0各完成142/163任务，无失败。
- 输入、段落、特征身份、排序、原文依据一致；selection_score、relevance、history_overlap及向量最大绝对差5.55e-17，低于1e-10容差。
- 小数据运行更慢，不能声称增加机器必定提高性能；这次也没有增加物理机器。

原始结果见comparison.json、local.json、standalone.json及日志。复现命令：`python3 scripts/compare_spark010.py`。当前配置的真实万条双语查询另见上级goal-retrieval.json，不混用两种验收范围。
