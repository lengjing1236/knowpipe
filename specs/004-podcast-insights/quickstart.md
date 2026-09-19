# 播客垂直切片验证

[方案] 安装 requirements.txt 与 Java 17+，准备真实 MongoDB；Web 与 worker 使用相同 MONGO_URI/MONGO_DB。

```bash
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=knowpipe_mining
export SPARK_LOCAL_IP=127.0.0.1
export SPARK_MASTER='local[2]'
python3 -m knowpipe.web.app
# 另一个终端使用相同环境变量：
python3 -m knowpipe.podcasts.worker
```

注册登录后添加 RSS。首轮只回填最新三集，后续每轮检查最近 100 集；无法获取 RSS 已不再提供的历史节目。暂只支持 RSS 2.0 与 Podcasting 2.0 官方 transcript 链接，不保证平台私有播客网址可用。

支持 UTF-8 纯文本、SRT、VTT 和 segments JSON；没有公开文字稿时显示等待状态，可在节目详情补充自有文字稿。页面文字稿全量保留，最长 400,000 字符，超过上限明确失败。按 800 字符切段仅是首版切片，不宣称语义段落或时间轴对齐。

有文字稿的节目依次进入 queued/processing/ready；失败最多自动重试三次。修改失败文字稿后可重新排队。处理完成后页面出现通知；断线重连可恢复，通知按用户和节目去重。取消订阅后不再产生新通知，历史通知保留但详情访问仍要求订阅。

分析页面展示中英文关键词及其权重、本批次主题和相似片段；不同批次的主题编号不可直接对比。单段/重复内容的 TF-IDF 可能无区分度，此时显式使用词频降级。没有官方时间戳的输入不展示虚构时间戳。

## 自动验证

```bash
python3 -m unittest tests.podcasts.test_feeds tests.podcasts.test_pipeline -v
SPARK_LOCAL_IP=127.0.0.1 python3 -m unittest tests.podcasts.test_analysis -v
```

真实库合成验收：启动独立 MongoDB 到 127.0.0.1:27028 后执行 PYTHONPATH=. python3 scripts/acceptance_podcast.py。脚本使用新的 knowpipe_acceptance_* 库，不操作课程库；合成 RSS 明确标记，不能作为真实节目或万条规模证据。

[待确认] 用户指定 RSS 与语言后，验证真实新节目更新、官方文字稿可用性以及是否需要自动语音转写；当前不承诺所有播客都提供文字稿。
