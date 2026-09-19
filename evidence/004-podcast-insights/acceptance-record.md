# Feature 004 验收记录

日期：2026-09-16。

## 本次补充：真实编程播客

- [已验证] 用户选择编程主题后，接入 JS Party RSS `https://changelog.com/jsparty/feed`，实际读取 2,270,738 字节。未确认 Matt Pocock 的可用 RSS，不将他标为数据来源。
- [已验证] **React: then & now（#349）**：官方 HTML 文字稿解析后 69,702 字符，88 段，批次 `20260916-7757495c`。
- [已验证] **WYSIWYG（#348）**：官方 HTML 文字稿解析后 75,171 字符，94 段，批次 `20260916-017c85ae`。
- [已验证] 两集共同使用真实 Spark `local[2]`、application id `local-1789563100288`，结果存入真实 MongoDB。另 1 集无官方文字稿，保留等待状态。没有注入 fetch 函数、替身数据库或生成文字稿。
- [已验证] 真实 HTTP 注册、登录、CSRF、详情、通知、匿名拒绝与两个账户隔离；强制再次抓取真实 RSS，目标节目、批次与通知没有重复。见 [详细 JSON](real-programming-podcast.json) 和 [运行日志](real-programming-podcast.log)。
- [已验证] Chromium 展示真实 React 文字稿及 88 个片段，1440px/390px 布局通过，JavaScript 异常 0；模拟 SSE 不可用时，5 秒轮询读到实际持久通知。见 [浏览器日志](defense-browser-local.log)、[桌面](local-real-podcast-desktop.png)、[文字稿](local-real-podcast-transcript.png)、[手机](local-real-podcast-mobile.png)。
- [已验证] 下载边界从 2 MiB 调至 4 MiB，保留公网地址校验及 400,000 字符限制；HTML 仅从 RSS 明确声明的文字稿地址读取发言者/段落。回归覆盖页面杂项排除与二次轮询不回填历史全集：[回归日志](real-feed-regression.log)。

[待确认] 本次是历史真实节目的业务链路验收；没有实测新节目发布延迟，也没有自动音频转写、万条规模或推荐语义质量的结论。

## 先前验收（合成切片及网络边界）

- [已验证] tests/ 中覆盖 RSS GUID 去重、文字稿格式、XXE、私有 IP/混合 DNS、重定向内网、响应超限、两用户隔离、手工文字稿、通知恢复/已读、订阅限额、失败重试和租约恢复。先行失败证据为 tests-red.log。
- [已验证] 真实 Spark 中英文输入与单段降级通过：[spark-tests.log](spark-tests.log)。最终全套日志见 Feature 005。
- [已验证] 真实 MongoDB + Spark 垂直切片：[real-mongo-spark.log](real-mongo-spark.log)。batch_id 为 20260916-fcc0b1c2；application id 为 local-1789559227614；真实 Spark local[2]，2 个文本段；重复轮询两次后仍是 2 集记录（1 集 ready、1 集 awaiting_transcript）和 1 条通知。
- [已验证] 该切片 RSS/文字稿为脚本明确构造的合成输入，fetch 函数注入；没有声称从公网抓取该节目，也没有用它证明万条规模或真实播客语义质量。
- [已验证] Gunicorn 实际 HTTP 接收持久 SSE 通知，浏览器打开文字稿与分段结果：[截图](browser-transcript.png)，详细日志见 Feature 003。
- [已验证] 公共网络抓取实测：https://feeds.twit.tv/twit.xml，读取 134,010 字节，标题 This Week in Tech (Audio)，最新 3 集均无支持的官方 transcript 标签；https://feeds.fireside.fm/linuxunplugged/rss 超过 2 MB 限制，正确返回 response_too_large。上述实测未证明完整真实播客分析链路。

[已验证] 用户目标已确定为编程播客，本次采用 JS Party。[待确认] 真实新节目到达。发现更新 ≤5 分钟、完成到通知 ≤10 秒为设计目标，本轮验证了通知路径但未完成长时间延迟测量。
[方案] 自动音频转写属于后续扩展；当前官方文字稿不存在时可手动提供文本，不能把节目简介当完整文字稿。
