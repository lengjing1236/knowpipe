# 本地中文翻译与播客转写

Feature 009 提供真实的免费 CPU 推理适配，Feature 010 增加中文目标到英文的查找解释，以及译文处理器和有限完整性检查。模型必须由运维人员显式准备；Web 和 worker 不会自动下载模型，也不会调用付费服务。默认没有模型时显示处理能力不可用。

## 准备与运行

需要 Python、ffmpeg/ffprobe，以及可选依赖：

```bash
python3 -m pip install -r requirements/media.txt
python3 scripts/acceptance_media.py --download-models --download-only
python3 scripts/prepare_goal_model010.py --download
```

正文翻译和 ASR 下载到被 Git 忽略的 `state/feature009/media/`，总压缩下载约 149 MB。中英目标模型另存 `state/feature010/media/`，74,481,402 字节；首次从官方 HTTPS 下载记录 SHA256，后续按证据中的 SHA 校验，下载上限 160 MB、解压上限 512 MB。正文/ASR 脚本校验固定文件大小和 SHA256。权重均不提交仓库。环境变量：

```bash
export KNOWPIPE_TRANSLATION_MODEL_PATH="$PWD/state/feature009/media/translate-en_zh-1_9"
export KNOWPIPE_ASR_MODEL_PATH="$PWD/state/feature009/media/faster-whisper-tiny"
export KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH="$PWD/state/feature010/media/translate-zh_en-1_9"
export KNOWPIPE_MODEL_THREADS=2
```

容器内应改为实际挂载目录；中英目标模型需要单独只读挂载。`configured_translator()`、`configured_goal_translator()` 和 `configured_transcriber()` 惰性加载，导入 Web 模块不会加载推理库。正文阅读提供方仍只支持明确标记为英语的全文到中文；`und` 和其他语言明确返回不可用。目标解释使用单独的中→英提供方，保留原中文分支；模型不可用时继续原文查找并显示状态，不能把语言失败说成资料库不存在相关知识。`translated` 仅表示处理完成，不代表目标语义正确。

默认正文翻译时限 1800 秒、目标转换 180 秒、ASR 时限 7200 秒，可分别用 `KNOWPIPE_TRANSLATION_TIMEOUT_SECONDS`、`KNOWPIPE_GOAL_TRANSLATION_TIMEOUT_SECONDS`、`KNOWPIPE_ASR_TIMEOUT_SECONDS` 调整。模型时限在处理块/音频段之间检查，不能强制中断正在执行的 C++ 推理调用。低内存机器应串行运行模型和 Spark 重任务。

## 处理边界

- 原文先分析，资料进入推荐结果后调用 `translate_document()`；仅复用正文版本、处理器身份与检查版本均相符的成功译文。模型仅作为辅助服务，不承担 Spark 推荐算法。
- 本地处理器标识包含模型权重、词表、配置、SentencePiece、语言方向和适配器版本的 SHA256；接口只显示有限标识，不暴露路径。模型目录在 provider 生命周期内视为不可变；升级使用新目录并重启 worker。自定义提供方应设置版本化 `processor_id`，只依赖类名的兼容适配无法识别其内部版本变更。
- 译文发布、失败状态和质量报告都校验正文版本、当前预期处理器和 worker 运行代数 `generation`。文档记录递增 `translation_generation`：较旧代数即使重新读取文档也不能接管新代任务，同一代数不允许切换处理器。未绑定代数的独立适配调用仍兼容；已绑定的文档拒绝无代数写入。worker 另在每篇开始前检查任务与租约，模型更新后旧译文不会继续作为当前译文。
- `translation_quality` 的状态仅为 `checks_passed` / `failed` / `not_checked`。本轮检查阿拉伯数字和百分号的缺失，以及 Markdown 围栏、缩进和行内代码的缺失；允许全角数字和千分位差异。数字写成汉字可能被保守阻止。缺陷以 `number_missing` / `code_missing` 显示，并设置 `translation_quality_failed`，不发布为中文就绪或产生就绪通知。
- `checks_passed` 不检查数字重复添加、术语误译、一般文字遗漏、数字语义关系，也不能可靠保护没有 Markdown 标记的代码。所有自动译文均显示 `human_reviewed=false`、`semantic_verified=false`，通过完整性检查不代表教学质量达标。
- 翻译保留 Markdown 围栏、缩进代码和行内代码；正文逐句分块，单次最多 192 输入 token，关闭引擎的静默输入截断。每个输出必须有结束标记，所有块成功后才返回完整译文；中途失败不返回部分译文。
- 音频通过 `transcribe_url(url, transcriber, AudioLimits())` 获取。默认最多 128 MiB、2 小时；下载期限 120 秒、解码期限 180 秒，管理员可用 `AudioLimits` 调整。
- 本地 ASR 在下载音频前检查四个模型文件是否存在且非空；缺失时立即报告能力不可用，不下载音频。携带节目元数据的代理也执行此检查，只有 `transcribe(Path)` 方法的其他适配器仍可正常接入。
- 翻译失败只保存受控错误码：`translation_empty_output`（空输出）、`translation_truncated`（缺少结束标记）、`translation_incomplete`（未完成或目标语言不符）、`translation_timeout`、`translation_invalid_input` / `translation_invalid_output`；配置类错误区分模型不可用和语言不支持。未知异常保留通用失败码，不保存异常消息或部分译文。
- 下载仅允许公网上的 HTTP/HTTPS 标准端口，连接固定到验证过的 IP，每次重定向重新验证，HTTPS 保留主机证书检查，禁用环境代理。流式计量实际字节数，拒绝超限、非完整响应或长度不符。
- ffprobe 和 ffmpeg 限定本地文件协议及普通音频/媒体容器，拒绝 HLS 等播放列表。再转为单声道 16 kHz WAV 并核验实际解码时长，防止只依赖文件头的时长声明。临时下载及 WAV 在成功或失败后都清理。
- ASR 完整消费模型返回的音频段，将因解码窗口切开的未完句合并为句段后再交给翻译。空结果、错误语言标记、中途异常和超时均不发布完整文字稿。Whisper 的识别错误和遗漏仍可能存在，完整处理不等于逐字正确。
- 可选 `transcriber.with_context(title=节目原标题, feed_title=订阅标题)` 仍返回 `transcribe(Path)` 适配器，复用本地模型。只把真实发布方元数据作为术语提示，不注入参考稿；上下文不会泄漏到后续无上下文调用。该改进需实测，不能因为标题中的词出现在输出就声称独立识别准确率提高。

生产流水线的持久缓存是数据库中带版本的译文、文字稿和成功目标解释；目标解释缓存同时绑定原目标、处理器和查询规则，不缓存失败。模型验证脚本的缓存仅用于可复现实验。没有通过 HTTP 接口开放任意本地模型路径。

## 模型与许可溯源

| 用途 | 固定模型 | 获取与许可说明 |
|---|---|---|
| 英中翻译 | Argos `translate-en_zh-1_9`，70,743,021 字节 | [官方模型索引](https://github.com/argosopentech/argospm-index/blob/main/index.json)提供下载；下载包 README 标明原始 OPUS-MT 模型为 CC BY 4.0，作者 Jörg Tiedemann、Santhosh Thottingal。权重不随本仓库分发 |
| 中英目标解释 | Argos `translate-zh_en-1_9`，74,481,402 字节 | 同一官方索引；本地下载包 README 标明原始 OPUS 模型 CC BY 4.0，同上两位作者。来源、观察 SHA 和处理器指纹记录在 `evidence/010-quality-cluster-readiness/goal-model.json` |
| 多语言 ASR | `Systran/faster-whisper-tiny`，revision `d90ca5fe260221311c53c58e660288d3deb8d356` | [官方模型仓库](https://huggingface.co/Systran/faster-whisper-tiny/tree/d90ca5fe260221311c53c58e660288d3deb8d356)标记 MIT；默认经 hf-mirror 获取同一版本并校验文件 SHA256，可用 `--model-mirror https://huggingface.co` 改为直连 |

实现使用 [CTranslate2](https://opennmt.net/CTranslate2/python/ctranslate2.Translator.html) 与 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)，CPU int8、默认 2 线程。解码器最大输出及结束标记检查用于发现工程截断，不是语义完整性判定。

## 验证与质量限制

```bash
python3 -m unittest tests.learning.test_local_providers tests.podcasts.test_audio -v
python3 scripts/acceptance_media.py
python3 -m unittest tests.learning.test_translation_quality
python3 scripts/acceptance_media010.py
```

第一条检验处理与失败边界，第二条真正加载权重，对多段技术英文和公开短音频推理，写入 `evidence/009-fulltext-podcast-learning/media-acceptance.json`。该短音频是 faster-whisper 测试中的 JFK 公开演讲片段，不能代表技术播客识别质量。技术播客另行运行并记录来源，不以剪辑后的片段冒充完整节目。

初次技术英文实测发现 Argos 将数据库 `transaction` 译作“交易”、`roll back` 译作“倒转”。这些错误保留在原始证据中；当前模型是可运行基线，尚不能宣称满足教学术语质量。ASR 的 tiny 模型也可能误识技术缩写和专有名词。后续可替换更强本地模型或明确授权的服务，但应在同一批真实材料上比较质量和资源开销，而不是只检查处理状态是否成功。

Feature 010 冻结的 8 个中文目标均实际转换，但有“事务→service”“异常处理→Aberrant Mechanism”“协程→Processes”等错误。真实 WAL 全文通过有限完整性规则，却把 `fsync` 译为“一丝不苟”，并在标题中添加原文不存在的要求。全部输入和输出见 `evidence/010-quality-cluster-readiness/goal-interpretations.json`、`translation-quality.json`，复核见 `media-worklog.md`。这证实模型质量仍是后续阶段 B 的必要工作；不应拿工程处理成功充当中文知识讲解验收。010 脚本使用 MongoMock 隔离测试缓存和发布契约，不冒充实际 Mongo 集群验收。

完整技术播客及 RSS 验证可用：

```bash
python3 scripts/acceptance_media.py --technical-podcast --technical-only --rss-mongo-uri mongodb://localhost:27018
```

这会跳过通用演讲样本，在随机命名的临时数据库中，用真实 RSS 单项快照调用生产 worker，再实际下载完整音频、运行 ASR、发布统一全文，第二次强制到期轮询验证不会重复转写。默认清理临时数据库；仅在还需继续集成时加 `--keep-rss-db` 保留。这里选用 Last Week in AWS 的完整 200 秒技术节目，快照、音频、全部文字稿及译文保留在 ignored 缓存，Git 证据只记来源、哈希和处理结果。单项快照是人工选定的验收输入，不冒充自动发现实时节目；推荐排序和通知需由独立集成验收验证。
