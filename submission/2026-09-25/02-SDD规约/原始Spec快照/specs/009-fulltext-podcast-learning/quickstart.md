# Quickstart / Acceptance

[已验证：实现] 需 Python 3.10+、Java 17、MongoDB 7、ffmpeg/ffprobe；Web 请求不启动 Spark。模型仅从显式本地路径加载。

```bash
python3 -m pip install -r requirements/worker.txt -r requirements/media.txt -r requirements/test.txt
python3 scripts/prepare_fulltext_corpus.py --target 10000 --max-requests 180 --output state/feature009/corpus
python3 -m knowpipe.corpus.supplement --base state/feature009/corpus --output state/feature009/corpus-expanded
python3 -m knowpipe.corpus.verify --root state/feature009/corpus-expanded
python3 scripts/acceptance_media.py --download-models --download-only
```

采集先使用缓存、记录共享配额和 backoff，不绕过站点25页限制。媒体脚本默认缓存 `state/feature009/media`，镜像/固定版本/hash详见[模型说明](../../docs/local-media-providers.md)。下载模型是显式运维操作，worker不会暗中下载。

[方案：本地运行] 用自己的 MongoDB 设置环境后导入已核验 JSONL；保持三个终端的 MONGO_DB 一致。以下使用开发数据库，不读 `.env` 或已有凭据。

```bash
export MONGO_URI=mongodb://127.0.0.1:27017
export MONGO_DB=knowpipe_learning
export SPARK_MASTER='local[1]'
export SPARK_LOCAL_IP=127.0.0.1
export PYSPARK_SUBMIT_ARGS='--driver-memory 768m pyspark-shell'
export KNOWPIPE_TRANSLATION_MODEL_PATH="$PWD/state/feature009/media/translate-en_zh-1_9"
export KNOWPIPE_ASR_MODEL_PATH="$PWD/state/feature009/media/faster-whisper-tiny"
export KNOWPIPE_MODEL_THREADS=2
python3 -m knowpipe.recommendations.importer --input state/feature009/corpus-expanded/documents.jsonl
# 分别在三个终端启动：
python3 -m knowpipe.recommendations.worker
python3 -m knowpipe.podcasts.worker
python3 -m flask --app knowpipe.web.app:create_app run --port 8000
```

打开 `http://127.0.0.1:8000/learning`，注册/登录，保存目标；推荐入选后自动翻译，英文不直接冒充中文。RSS 在同页订阅，全部更新可展开，个人通知须通过目标/历史/版本和中文就绪条件。失败后可点“刷新推荐”重试翻译，或在节目上点“重试处理”。

[已验证：测试] 运行回归（包含真实 Spark 测试）或单独验证增量：

```bash
python3 -m pytest tests/learning tests/corpus tests/recommendations tests/podcasts tests/web -q
PYSPARK_SUBMIT_ARGS='--driver-memory 512m pyspark-shell' python3 -m unittest tests.recommendations.test_incremental -v
```

完整测试包含 Spark；在约7GiB的开发机上，真实模型推理、Spark测试、万条验收和外部评价按顺序运行。数据/音频/模型和中间索引均存 `state/`，不提交 Git。

[已验证：验收脚本] `scripts/acceptance_scale009.py` 在独立Mongo库导入真实万条语料，运行Spark索引及推荐并保存作业/阶段证据；`--keep-database` 可保留给 `scripts/acceptance_browser009.py`。后者使用同一次真实排名验证中文阅读、RSS表单、移动布局及目标变更清除旧结果。最终已执行命令和结果见[阶段验收](../../evidence/009-fulltext-podcast-learning/acceptance.md)。

完整验收按以下顺序执行，沿用上面的模型和 Spark 环境变量：

```bash
python3 scripts/acceptance_media.py --technical-only --rss-mongo-uri mongodb://127.0.0.1:27018 --keep-rss-db
python3 scripts/acceptance_scale009.py --input state/feature009/corpus-expanded/documents.jsonl --keep-database
python3 scripts/acceptance_browser009.py
python3 scripts/acceptance_podcast009.py
python3 scripts/acceptance_browser_rss009.py
```

均在独立的本机 Mongo 测试库运行；需先启动端口27018的 Mongo。媒体脚本参数为 `--rss-mongo-uri`，其他验收脚本为 `--mongo-uri`，可以指向其他测试实例。浏览器脚本需可用 Chromium 及本地配置 `/tmp/knowpipe-chromium.json`（`executable` 和 `args`），这是当前开发机验收配置，不代表容器里已安装浏览器。播客集成重放同一次实际ASR产物，不重复宣称一次新的实时RSS发现。

[实现边界] 当前英中翻译为小型 Argos 模型，技术术语可能误译；Whisper tiny 的完整处理不保证每个词准确。模型时限在块/段之间检查。当前运行是单机Spark，不宣称真实多机集群。Compose 的worker已加入模型只读挂载，容器构建需在有Docker的环境另行验证。
