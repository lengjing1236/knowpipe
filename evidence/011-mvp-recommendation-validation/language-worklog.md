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

回归：`python3 -m unittest discover -s tests/learning -p 'test_*.py'`，75 项通过。测试证明接口、版本保护和已知损坏拒绝，不证明翻译语义质量。
