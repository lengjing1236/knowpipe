# 012 演示快速开始

[已验证] 本机MongoDB运行于127.0.0.1:27018。演示使用独立knowpipe_demo012数据库和全部12篇中文全文；不覆盖旧库。首次计算含索引可能数分钟，真实时间见012证据。

```bash
python3 scripts/demo_release.py prepare
python3 scripts/demo_release.py start
python3 scripts/demo_release.py status
```

打开 http://127.0.0.1:8019/learning ，本机账号和随机密码见`state/demo-release/account.json`。已运行时无需再次start；停止用`python3 scripts/demo_release.py stop`，会保留资料和记录。

演示目标：学习 Python 的异常捕获、异常传播和日志记录。
操作：保存目标→等待实际推荐→展开原文依据→中文全文→主动标记已读→查看历史→等待重算。排序质量尚未达标，不应把出现的每篇资料都说成准确答案。

免费远程翻译：配置服务端环境`KNOWPIPE_BIGMODEL_API_KEY`，或写入本机私有`state/demo012/bigmodel-api-key`（权限0600），stop/start使其生效。密钥不提交Git。认证或限流失败不启动本地翻译；原生中文无需模型。

```bash
python3 scripts/record_delivery012.py --credentials state/demo-release/account.json --goal '学习 Python 的异常捕获、异常传播和日志记录'
```

录制要求初始阅读历史为0。已录过的账号请通过页面撤销已读或使用新的隔离库与state路径，不直接改写准备好的推荐结果。原始视频保存在ignored的state/delivery012；提交MP4只剪实际等待并加说明字幕。

六项材料位于submission/2026-09-25。`scripts/build_delivery012.py --package`在视频和PPT存在且校验通过后打包。输出包不含本机账户密码、模型权重、音频缓存或API密钥。
