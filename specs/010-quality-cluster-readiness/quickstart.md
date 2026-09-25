# Quickstart

[已验证] 沿用009环境和已准备的全文库。需要Python、Java17/PySpark、Mongo与可选媒体依赖；见[媒体说明](../../docs/local-media-providers.md)。验收脚本不会替你配置正式数据库或自动升级模型。

## 准备模型与运行工作台

```bash
python3 scripts/prepare_goal_model010.py
export KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH="$PWD/state/feature010/media/translate-zh_en-1_9"
export KNOWPIPE_TRANSLATION_MODEL_PATH="$PWD/state/feature009/media/translate-en_zh-1_9"
export KNOWPIPE_MODEL_THREADS=2
export SPARK_MASTER='local[1]'
python3 -m knowpipe.recommendations.worker --index-root state/recommendations
```

Web与worker使用相同的MONGO_URI/MONGO_DB。在另一终端运行 `python3 -m knowpipe.web.app`，登录 `/learning` 后保存中文目标。查看自动英文解释、技术对象依据及阅读详情的译文检查说明。解释可能误译，不能将其当作正确答案。模型升级后重启worker，版本化任务重新生成；临时转换失败可点击“刷新推荐”重试。

## 本次验收命令

```bash
python3 -m pytest tests/learning tests/podcasts tests/corpus tests/recommendations --ignore=tests/recommendations/test_engine.py --ignore=tests/recommendations/test_incremental.py -q
python3 -m pytest tests/recommendations/test_engine.py tests/recommendations/test_incremental.py -q
python3 scripts/acceptance_media010.py
SPARK_MASTER='local[1]' PYSPARK_SUBMIT_ARGS='--driver-memory 1g --conf spark.ui.retainedJobs=40 --conf spark.ui.retainedStages=80 pyspark-shell' python3 scripts/acceptance_query010.py
python3 scripts/compare_spark010.py
python3 scripts/acceptance_browser010.py
```

按顺序运行；第一组也包含旧播客Spark测试，不是纯单元测试。目标验收复用009的冻结索引及本次实际模型输出，不把错误译文换成手写英文。浏览器脚本需既有Chromium运行配置 `/tmp/knowpipe-chromium.json`，默认连接本机27018，只创建/清理自身随机命名数据库，展示真实已计算结果；不是持续运行服务的验收。

同机对照使用12篇明确标注的合成资料、原生Standalone两个executor，用于验证部署及一致性。不是万条性能基准，也不是多台机器。详见[部署说明](../../docs/spark-deployment-readiness.md)。跨主机需要共享挂载、同版依赖与driver连通，当前只有一台电脑，该验收仍待资源。

[边界] 首次640MiB检索验收发生Java heap不足；1GiB和16MiB读取分区目标完成全部16次检索。该目标不是解压内存硬上限。真实翻译仍有严重术语错误，部分目标返回不合适资料，详见[010验收](../../evidence/010-quality-cluster-readiness/acceptance.md)。阶段B/C/D仍未完成。
