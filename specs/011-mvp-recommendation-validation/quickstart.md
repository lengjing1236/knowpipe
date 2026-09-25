# 011 本机 MVP 验证

状态：实施与验收中，**MVP 尚未通过**。本页用于复验，命令存在不代表效果达标。实际结果见[追踪表](../../evidence/011-mvp-recommendation-validation/traceability.md)。本轮使用本机 Spark，不需要第二台主机。

## 1. 前提与输入

在仓库根目录执行，需要 Python 3.10、Java 17、MongoDB 和项目依赖。本机 MongoDB 为 `mongodb://127.0.0.1:27018`；其他机器按实际地址传参。

```bash
python3 -m pip install -r requirements.txt -r requirements/worker.txt \
  -r requirements/media.txt -r requirements/semantic.txt -r requirements/test.txt
python3 scripts/evaluate_mvp011.py verify
```

校验使用冻结的 `cases.json`、`cases.sha256`、`state/feature009/corpus-expanded/documents.jsonl` 和 `state/feature011/evaluation/controlled-documents.jsonl`。首次复现需按来源证据准备输入；`state/` 不入 Git，不能把代码存在当作模型／语料已存在。冻结案例不能依据输出更改；Docker、Django 保留场景只用于冻结实现后的最终验证。

本机已准备多语 MiniLM 包和 Qwen GGUF；新环境显式准备：

```bash
python3 scripts/prepare_semantic011.py --multilingual \
  --root state/feature011/semantic-multilingual --download
python3 scripts/prepare_qwen011.py --download
```

来源、版本／哈希见 `evidence/011-mvp-recommendation-validation/semantic-models.json` 和 `qwen-model.json`。Qwen 仍有关键技术误译，是本轮待验候选，不能据此宣称中文质量达标。

## 2. 本机运行环境

在执行验收或 worker 的终端设置：

```bash
export SPARK_LOCAL_IP=127.0.0.1
export SPARK_MASTER='local[1]'
export LEARNING_SPARK_PARTITIONS=4
export PYSPARK_SUBMIT_ARGS='--driver-memory 1g pyspark-shell'
export KNOWPIPE_SEMANTIC_MODEL_PATH="$PWD/state/feature011/semantic-multilingual"
export KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH="$PWD/state/feature011/media/qwen2.5-1.5b-instruct-q4_k_m.gguf"
export KNOWPIPE_LLAMA_TRANSLATION_URL=http://127.0.0.1:8089
```

在专用终端启动固定版本的翻译服务，完成后按 Ctrl-C 关闭：

```bash
state/feature011/media/llama-b6000/build/bin/llama-server \
  --model "$PWD/state/feature011/media/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
  --host 127.0.0.1 --port 8089 --ctx-size 4096 \
  --threads 2 --threads-batch 2 --parallel 1 --n-gpu-layers 0 \
  --batch-size 128 --ubatch-size 64 --no-context-shift --no-warmup \
  --alias knowpipe-translation-qwen25-15b
```

同一时刻只执行一个重型验收程序。并行开发不代表同时启动多个 Spark／模型任务；单个生产 worker 内部调用模型是实际链路的一部分。

## 3. 冻结案例与原文审核

输出到新目录，保留已提交的失败证据：

```bash
mkdir -p state/feature011/recheck
python3 scripts/evaluate_mvp011.py run --mode current --pool small \
  --split development --output state/feature011/recheck/current-dev-small.json
python3 scripts/evaluate_mvp011.py run --mode current --pool full \
  --split all --output state/feature011/recheck/current-full.json
python3 scripts/evaluate_mvp011.py review-template \
  --results state/feature011/recheck/current-full.json \
  --output state/feature011/recheck/current-full-review.json
```

评价程序保存实际输入、代码快照、模型身份、比较范围和原文位置。实施方必须核对每个推荐与每条补充断言的候选／历史原文，再填写审核文件；模板不是标签，不恢复已取消的用户手工标注任务。审核与结果 SHA 绑定，新结果不能直接复用旧审核。

`--mode baseline` 使用冻结旧代码包，须取消 Qwen 环境变量并配置 `KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH=state/feature010/media/translate-zh_en-1_9`。当前开发小池也可使用相同 Argos 隔离算法变化；完整背景验收则固定为与真实 worker 相同的候选配置。两种结果分开记录。受控镜像／改写即使传 `--pool full` 仍使用规定的小候选池，不算万条背景。`without_history` 消融仅在固定候选集关闭历史得分，不等于重新运行整个无历史管线。

## 4. 真实页面与增量通知

```bash
python3 scripts/acceptance_mvp011.py --output state/feature011/recheck/browser
python3 scripts/acceptance_replays011.py --output state/feature011/recheck/replays.json
python3 scripts/acceptance_rss011.py --output state/feature011/recheck/rss-real.json
```

浏览器脚本使用 Playwright 已安装的 Chromium；本机自备二进制可传 `--browser-settings /tmp/knowpipe-chromium.json`（JSON 包含 `executable`、`args`）。它创建隔离数据库，从页面保存中文目标，由真实 worker/Spark 计算并翻译，打开中文全文与对照，显式已读后重算，不注入 ready 任务；结束清理自身 Web、worker 和临时数据库。

三分支回放使用明确声明的受控 RSS 文字稿，检验补充／重复／无关的决策、中文就绪和通知幂等，不声称真实新播客。`acceptance_rss011.py` 另行回放完整发布方 RSS 条目，实际下载完整音频并 ASR；依赖已有 `state/feature009/media` 中经核验的 Whisper 模型和节目来源。它不代表长期实时订阅或学习质量验证。

## 5. 记录结论

工程回归、原文事实、中文质量、真实页面、增量通知、完整背景／保留场景分别留证。任一必需 SC 失败或缺证据，结论仍是 MVP 尚未通过；同步 `tasks.md`、根 `plan.md` 和追踪表，不降低门槛。
