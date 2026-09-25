# 011 语言实现与真实模型检查

日期：2026-09-25。当前结论：接口与保护已实现，中文技术语义尚未达标，不能将候选模型接入等同于 SC-004 通过。

## 实现

- `TextResult.segments` 保持旧接口兼容；实际翻译时记录原文/译文 Unicode 码点区间，连续覆盖全文，保护代码和空白。无对齐的旧提供器返回空列表，不猜测对应。
- 完整性 v2 检查数字缺少/额外重复、代码损坏、语法可识别标识符丢失和新增未知字符。所有检查仍标识 `semantic_verified:false`。它们不能发现所有省略、技术术语及因果关系错误。
- `configured_language_provider`、全文和目标翻译工厂可通过显式 `KNOWPIPE_NLLB_MODEL_PATH` 复用同一双向 CT2 实例，不自动下载。适配器含模型哈希与规则版本，内部串行调用，保持原有 CAS 和版本保护。
- `translation_segments` 仅随全文详情返回，缓存命中必须满足处理器与检查版本。前端偏移需按 Unicode 码点处理，不能直接当 UTF-16 索引。
- 新增 `scripts/prepare_language011.py`、`scripts/acceptance_language011.py` 和对齐/损坏/双向共享测试。

## 数据和来源

使用 [OpenNMT 官方论坛教程](https://forum.opennmt.net/t/nllb-200-with-ctranslate2/5090)发布的 NLLB 600M CT2 INT8 与 SentencePiece。模型 [Meta 原始说明](https://huggingface.co/facebook/nllb-200-distilled-600M)定位通用领域、句子级研究模型，CC-BY-NC-4.0；不由模型卡推断技术全文质量。

实际下载 583,438,724 字节，安装 630,016,680 字节，符合 650 MB 下载和 1 GB 解压边界。原始 URL、首次观察 SHA、许可在 `language-model.json`。没有冒充发布者提供的校验值。

## 已执行开发 A/B

`language-quality.json` 保存 8 个 010 旧目标、3 个新的开发目标及完整 PostgreSQL WAL 正文。未读取保留场景运行结果来改代码/词表。所有文本均经过真实模型推理；Argos 对照使用历史原输出。

NLLB 峰值约 785 MiB RSS，WAL 全文处理 53.2 秒，首次冷加载加第一句曾用 51 秒，后续进程热文件缓存约 10.5 秒。

失败示例：异常处理→Unusual processing、协程→syntax、数据库事务→database issues、序列化→sequencing。WAL 中遗漏日志顺序写入的原因与最后一段重放步骤，point-in-time recovery→实时恢复。`溃` 字在模型词表中缺失，译文出现 `⁇`，完整性检查会拒绝该输出。改善例：事务隔离的 transaction isolation、保留 fsync；这些改善不能抵消实质错误。

结论：保留可替换提供器及对齐实现，但 NLLB 600M **不作为已验证的默认质量升级**。SC-004 继续失败。模型处理完整、对齐完整和数字检查均不能替代语义验证。

## Qwen 后续候选真实检查

[Qwen 官方 Qwen2.5-1.5B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)支持中英，Apache-2.0。Q4_K_M 文件实际 1,117,320,736 字节，固定 revision 和 Git-LFS SHA 已验证；本机直连 HF 超时，实际通过 hf-mirror 传输。首次下载中断后改成有界 HTTP Range 续传，失败不伪报完成。llama.cpp 官方 b6000 CPU 预编译包 13,113,111 字节，固定官方 release SHA；通过 GitHub release asset API 下载，实测 glibc 2.35 兼容，无需编译、Torch 或付费服务。详见 `qwen-model.json`。

提供器只连接显式 `http://127.0.0.1` 服务，核对 `/props` 的模型路径、引擎 commit、4096 token 上下文。通过实际聊天模板与分词接口计数，超过 1024 输入 token 时分段，输出上限 2048；EOS 之外的终止或上下文截断都拒绝。固定通用技术翻译 prompt，不包含场景答案和术语替换表。与现有提供器共享 `TextResult`/版本发布协议。

三项此前失败的旧目标 smoke 中，异常处理、协程、数据库事务均被正确保留（`qwen-smoke.json`）。完整 11 目标和 WAL 检查见 `qwen-quality.json`：8 个旧目标正确保留关键概念，但 Python 开发目标仍将信号量译成 signals。完整 WAL 用时 49.6 秒，数字/代码完整性通过，却把“日志记录期间的数据刷新可禁用”误译成“日志记录可禁用”；首段先日志落盘后写数据文件的约束也表达混乱。因此 SC-004 仍未通过，不把 Qwen 称为已完成的质量升级。

模型服务实际使用 2 推理线程、4096 上下文、1 槽、0 GPU；默认 batch 下峰值 RSS 1,929,716 KiB，已记录并在本轮 A/B 后停止服务释放资源。运行时占用高于 GGUF 文件大小，后续与 Spark 同时运行前需保留这一实际约束。

## 最后一次有界修复：自然句对齐

保留同一个模型和通用翻译 prompt，仅把 Llama 适配器升级为 `v2:sentence`：按自然句保存原始标点/空白跨度，对特别长的句子仍先用实际 tokenizer 检查上限并分段。每个实际输入片段各自记录源/目标偏移。模型、prompt、切分/解码规则和固定引擎版本共同参与处理身份。

同 11 个目标和完整 WAL 的新输出及旧段级输出并列保存在 `qwen-sentence-quality.json`，没有覆盖先前失败记录。目标输出没有因为切分改变，8 个旧目标的主要概念正确，Python 开发目标的“信号量→signals”仍未修复。

全文先日志落盘再写数据文件的表述更清楚，顺序写入和 fsync 的主要关系保留；但出现明确错误：`roll-forward recovery` 被译成“回滚恢复”，`point-in-time recovery` 被译成“点对点恢复”，`data flushing during journaling` 的操作对象仍变成“日志刷盘”。JSON 审核记录包含这些判断对应的双方原文及实际偏移。数字/代码等完整性检查仍通过，进一步说明其不能证明语义正确。

本轮使用 batch 128、ubatch 64、4096 上下文，全文 76.485 秒，峰值 RSS 1,896,336 KiB（约 1.81 GiB）。内存只比旧配置略降，不能以 GGUF 文件大小推断运行占用。确认服务所有 slot 空闲且 8089 没有已建立连接后，已停止本轮独占 PID 109160 并释放资源。

结论：**SC-004 仍未通过**。本轮不再换更大模型或无限修改提示词；保留可替换接口、真实对齐与失败证据，不把当前 Qwen 配置作为已经通过质量验证的默认升级。

## 固定本轮待验候选与复现方式

最终选择 **Qwen 段落 v1**。句切 v2 改善一处先后关系，却新增“向前恢复→回滚恢复”和“时间点恢复→点对点恢复”两个明确的技术含义错误；不能因切分更细就认为质量更高。段落 v1 的错误仍包括信号量→signals、数据刷新被换成日志记录、WAL 写入顺序表述混乱，所以该选择只固定后续评价和 Web 使用的候选，**不代表 SC-004 已达标**。

`LlamaTranslator` 默认 `segmentation='paragraph'`，恢复原 v1 处理身份与相同通用 prompt；显式 `segmentation='sentence'` 可重现 v2 实验，两个模式的缓存身份不同。原始两份失败输出均保留，没有重新跑模型或补写成功结果。

复现时从仓库根目录，在一个终端启动本地服务：

```bash
state/feature011/media/llama-b6000/build/bin/llama-server \
  --model /home/lengjing1236/knowpipe/state/feature011/media/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8089 --ctx-size 4096 \
  --threads 2 --threads-batch 2 --batch-size 128 --ubatch-size 64 \
  --parallel 1 --n-gpu-layers 0 --no-context-shift --no-warmup \
  --alias knowpipe-translation-qwen25-15b
```

在另一个终端运行正式检查 CLI。`--output` 必须使用未存在的文件名；脚本拒绝覆盖原失败记录，仅读取 `language-quality.json` 中的 8 个旧目标、3 个开发目标及 WAL 原文，不读取保留场景：

```bash
python3 scripts/acceptance_qwen011.py \
  --segmentation paragraph --output qwen-paragraph-recheck-01.json
```

若复现句切失败，将 `--segmentation` 改为 `sentence`，并指定另一个未存在的输出文件。`--smoke` 只运行三个旧目标，不能作为全文通过证据。完成后在服务终端按 Ctrl-C 停止服务。

Web/worker 进程使用下列配置共享该双向提供器；不会自行下载模型或启动服务：

```bash
export KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH=/home/lengjing1236/knowpipe/state/feature011/media/qwen2.5-1.5b-instruct-q4_k_m.gguf
export KNOWPIPE_LLAMA_TRANSLATION_URL=http://127.0.0.1:8089
```

最终轻量回归：`python3 -m unittest discover -s tests/learning -p 'test_*.py'`，79 项全部通过（16.177 秒）；适配器与检查 CLI 的 `py_compile`、`git diff --check` 通过。新增检查确认段落默认上下文、模式间缓存隔离，以及 CLI 在加载模型前拒绝覆盖已有证据。这些测试验证接口和保护逻辑，不替代上述真实模型语义失败结论。
