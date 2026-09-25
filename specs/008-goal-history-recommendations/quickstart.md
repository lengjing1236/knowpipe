# 第二阶段运行与验证

[已验证] 本地先启动 MongoDB，在两个终端分别运行 Web 与独立推荐 worker，二者使用相同数据库：

```bash
python3 -m knowpipe.web.app
python3 -m knowpipe.recommendations.worker --mongo-db knowpipe_mining
```

[已验证] worker 复用 Spark 会话，默认 `local[2]`；Web 不启动 Spark。打开 `/learning`，保存目标或点击刷新推荐，等待后台处理。仅有旧摘要语料时明确无合格全文，不会自动把它们升级。目标和已读修改会自动产生新任务；原文先分析，入选英文正文才准备译文，尚未配置提供方时如实显示不可用。

[已验证] 可用 `python3 -m knowpipe.recommendations.importer --input PATH --mongo-db NAME` 导入已核验全文的 JSONL。每行需包含 `source/doc_id/title/body_text/language/source_url/license/fulltext_verified`，最后一个字段必须为 `true`。声明非质量检测；导入前必须核实提取的是完整正文。逐行提交，遇到错误报告行号；修正后可幂等重跑。

[已验证] 小样例获取和验收命令：

```bash
python3 scripts/prepare_recommendation_sample.py
PYTHONPATH=. python3 scripts/acceptance_recommendations.py --mongo-uri mongodb://localhost:27017
python3 -m unittest discover -s tests/recommendations -v
```

[已验证] 第一条只抓取明确列出的 12 篇 Python/Django 官方中文页面，原 HTML、正文、许可保存在被忽略的 `state/feature008/sample/`。第二条要求可运行的 Mongo、Java/Spark、Playwright Chromium，使用并清理独立临时数据库；浏览器可通过 `BROWSER_EXECUTABLE` 指定。结果、来源清单和截图见 [验收目录](../../evidence/008-goal-history-recommendations/)。如果要在自己的本地工作台阅读这些样例，显式运行上述 importer 并指向 Web 使用的数据库；验收脚本不会向项目数据库注入测试用户或资料。

[已验证] Compose 新增 `recommendation-worker`，沿用 worker 镜像，缓存卷挂载到 `/var/lib/knowpipe/recommendations`。本环境验证 YAML 结构和原生进程运行，未执行 Docker 镜像构建。

[已验证／运行边界] 空闲时约每 5 秒扫描目标，约每 60 秒检查全文快照，长任务完成后再检查下一轮。正文变化会重建快照，当前不是流式增量索引。任务租约 180 秒、每 15 秒续租、最多 3 次尝试，点击刷新可开启新的有限重试周期。快照保留历史版本，需要管理磁盘占用；跨主机部署需共享快照存储，不能只替换 master 地址。

[已验证／算法边界] 先以正文目标覆盖至少 50% 和余弦门槛召回最多 100 篇，每篇保留 3 个相关段落；参考版本一致的历史并去重后最多返回 10 篇。当前语料上限 50,000 篇/200,000 段，历史上限 2,000 篇/5,000 个相关段；超限失败，不静默冒充全量分析。基线/消融保存在任务结果中，缺少独立标签时不能据此计算学习效果。
