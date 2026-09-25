# 免费远程翻译接入

演示版本默认仅使用智谱官方远程 `glm-4.7-flash`，不配置密钥时明确显示翻译服务未配置，不调用本地翻译模型、不切换收费型号。原生中文全文无需翻译，可以直接演示阅读流程。

在启动工作者的环境中设置 `KNOWPIPE_TRANSLATION_PROVIDER=bigmodel-free`，通过本机受保护环境设置 `KNOWPIPE_BIGMODEL_API_KEY`；不要将密钥写入 Git、交付包或录像。全文英译中与学习目标中译英共用同一提供方。`KNOWPIPE_TRANSLATION_TIMEOUT_SECONDS` 默认 600 秒，单次 HTTP 最长 90 秒；429 最多尝试 3 次且只在短等待后重试，长等待交由现有工作者的有限重试流程处理。

固定端点：`https://open.bigmodel.cn/api/paas/v4/chat/completions`。固定型号无法通过配置替换成付费型号。官方价格与限流需要以服务实时政策为准：[价格](https://docs.bigmodel.cn/cn/guide/start/pricing.md)、[限流](https://docs.bigmodel.cn/cn/api/rate-limit.md)。若免费政策变化，应停止该服务；本适配器不自动查询价格或充值。

适配器保留完整段落语境，代码块原样保留，行内代码用带原代码上下文的占位符保护，并生成真实字符区间对齐。拒绝截断响应、型号不匹配、空输出和受损占位符；单段超过 6,000 个字符明确失败，不静默截断。发布前仍执行原有数字、代码、标识符完整性检查。通过这些检查不能证明技术含义准确。

缓存绑定原文版本及服务、型号、提示词和适配器身份。服务端型号别名可能更新，无法把它当作固定权重；复核服务更新后可提高 `KNOWPIPE_REMOTE_TRANSLATION_REVISION`（默认 `v1`）使旧译文重新处理。历史本地适配器保留用于旧实验复现，只有显式 `KNOWPIPE_TRANSLATION_PROVIDER=local` 才能选中；交付演示启动器必须设置 `bigmodel-free`。

当前验证范围：模拟 HTTP 的接口测试已通过。2026-09-25 对 3 个完整公开技术段落与 1 条中文目标发起真实请求，均未取得译文；后续有限诊断确认官方端点返回 HTTP 401（认证未通过）。原始失败记录见 `evidence/012-demo-delivery/remote-translation.json`，诊断只记录状态码，不保存密钥或服务端错误正文。当前不能声称远程翻译质量已通过或误译问题已经解决。

配置有效密钥后可运行 `python3 scripts/verify_remote_translation012.py --output evidence/012-demo-delivery/remote-translation-retry.json`，脚本从 `state/demo012/bigmodel-api-key` 读取密钥并保留原文、译文、耗时及完整性检查结果。它不会让翻译模型自评，也不会自动将完整性检查判作技术含义正确；需要逐项对照原文的条件、否定和操作顺序。已有真实结果不会被脚本覆盖。
