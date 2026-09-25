# Spark 运行准备与单机对照

当前只有一台电脑。本说明区分三个事实：`local[2]` 在同一 JVM 中使用两个计算线程；本机 Standalone 可以运行独立的 executor JVM；真实跨主机需要至少两台可互通的机器。下面的验收只证明前两种执行方式，不证明跨主机部署或加速。

## 这次修改解决什么

以前 worker 每次调用 `.master(SPARK_MASTER 或 local[2])`，可能把 `spark-submit --master spark://…` 覆盖成本机执行。现在 `runtime.create_spark()` 按“显式 `SPARK_MASTER` → 已提交的 Spark 配置 → `local[2]`”选择；分区数也保留提交配置，除非显式设置 `LEARNING_SPARK_PARTITIONS`。已有 SparkContext 与要求的 master 不符时明确失败。

目前快照、JSONL 与 Parquet 使用本地路径 API。Standalone 因此要求 driver 与所有 executor 将同一个共享目录挂载在**同一绝对路径**，并通过 `KNOWPIPE_SPARK_SHARED_ROOT` 声明；这并不是已接入 HDFS。启动探针让真正的 Python worker 读取 driver 随机标记、写回校验结果，并检查 Python 版本、jieba/PySpark 版本与分词、评分、目标规则及其内容依赖模块的代码指纹。只声明目录而 executor 无法读写时，启动会失败。

探针记录 Python 进程、主机、任务编号和 Spark 自己的 executor ID、任务数。它只能证明实际参与探针的执行器；后加入或故障恢复的节点仍需独立验证。探针通过也不代表所有网络负载、长时间运行或存储容灾已经验收。

## 本机执行

需要已有 Java、Python 与项目 worker 依赖，以及 PATH 中的 `spark-submit`。代码提交工具自动将 `knowpipe/*.py` 打为 zip，通过 `--py-files` 分发；第三方二进制依赖不打进 zip。

```bash
python3 -m unittest tests.recommendations.test_runtime
python3 scripts/submit_recommendations.py \
  --master 'local[2]' \
  --index-root "$PWD/state/recommendations" \
  --preflight-output "$PWD/state/spark-preflight.json"
```

启动现有推荐 worker，Mongo URI 沿用环境变量，避免写入部署报告：

```bash
python3 scripts/submit_recommendations.py \
  --master 'local[2]' \
  --index-root "$PWD/state/recommendations" -- --once
```

在其他 Spark/模型任务停止后执行固定输入对照（约需 3 GiB 可用内存）：

```bash
python3 scripts/compare_spark010.py
```

工具顺序运行本地计算与本机原生 Standalone：启动一个 master、一个双核 worker，并运行两个单核、512 MiB executor。端口动态分配，仅绑定本机地址；成功、失败或中断均清理本工具启动的进程组。它不用 `local-cluster` 模拟器，也不使用 Docker。

输出位于 `evidence/010-quality-cluster-readiness/spark/`，包括原始日志、两种运行的完整结果与 `comparison.json`；中间文件在忽略的 `state/feature010/spark-comparison/`。输入是明确标注的 12 篇合成技术资料，只验算同一算法、输入、排序及原文证据是否一致，不是新增的公开语料效果评测。数值容差为绝对误差 `1e-10`，同时记录实际最大差值。耗时包含启动，不能用这个小样本推断大数据加速比。

最终集成后重新运行对照，当前目录保留最终代码指纹。较早成功记录移至 `spark-attempt2-before-final-integration/`，首轮提交模式错误保留于 `spark-attempt1-submit-master-failure/`，没有回写历史记录。具体版本边界见当前证据目录的 `README.md`。预检查失败时，单独执行预检查的 JSON 会保留参与任务与缺失依赖、代码指纹不一致或共享路径读写失败的诊断。

万条索引的压缩Parquet在默认128MiB读取目标下可能被合并成过大的缓存块。当前默认16MiB读取分区目标，保留显式提交覆盖；真实开发查询采用1GiB driver通过。该配置不是解压后内存硬限制，单个大记录/row group及shuffle仍需根据实际负载测量，不能通过添加worker掩盖单任务内存峰值。

## 将来跨主机必须完成的事情

1. 各主机安装相同 Python 主次版本、Java、PySpark、jieba 及 worker 依赖；本项目代码由提交工具分发。ASR/翻译仍在业务处理进程运行，并不会因添加 Spark executor 自动加速。
2. driver 与 worker 的共享挂载具有相同绝对路径及读写权限，快照和索引都位于其中。当前只是 POSIX 共享挂载适配，迁移 HDFS/S3 需要进一步替换文件创建、原子替换与缓存有效性逻辑。
3. 启动 Standalone master/worker，给 driver 配置所有 worker 能访问的地址。driver、master、executor 的 RPC 与块传输必须可互通；需要固定端口时使用 Spark 的 `spark.driver.port`、`spark.blockManager.port` 等部署配置。
4. 在相同版本的真实数据快照上运行预检查与比较，确认确有来自不同物理主机的 executor 完成实际推荐任务，并记录资源与负载。现有程序不会仅凭不同进程、主机名或容器数量宣布“跨主机验收通过”。

假设管理员已完成 `/srv/knowpipe` 的共享挂载和集群部署，提交示例为：

```bash
export KNOWPIPE_SPARK_SHARED_ROOT=/srv/knowpipe
python3 scripts/submit_recommendations.py \
  --master spark://MASTER_HOST:7077 \
  --driver-host DRIVER_HOST \
  --index-root /srv/knowpipe/recommendations \
  --preflight-output /srv/knowpipe/preflight.json
```

将示例中的主机名换为实际部署地址，检查报告后再去掉 `--preflight-output` 启动 worker。若模型只安装在 driver，属于预期：目标转换与全文翻译不在 executor 内执行。

## 对最终用户效果的影响

相同语料、目标和算法在单机或多机运行时，推荐结果应在浮点容差内一致；增加机器不会修复不相关材料或错误翻译。多机的价值是分担更多全文的分词、向量计算、筛选与排序，并可能缩短新增材料进入推荐的等待。能否更快取决于实际数据规模、CPU、内存、网络和共享存储；小数据可能因为通信与调度开销更慢。Web 可用性、Mongo 高可用和模型质量仍是独立问题。

依据：[Spark 集群概述](https://spark.apache.org/docs/latest/cluster-overview.html)、[提交应用与代码分发](https://spark.apache.org/docs/latest/submitting-applications.html)、[Standalone 部署](https://spark.apache.org/docs/latest/spark-standalone.html)。
