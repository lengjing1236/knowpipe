# 第一阶段验证

[方案] 在已有 Python 环境与 MongoDB 配置下运行，复用 requirements.txt；不需要模型服务。

```bash
python3 -m unittest discover -s tests/learning -v
python3 -m unittest discover -s tests/web -v
node --check knowpipe/web/static/learning.js
python3 -m knowpipe.web.app
```

[已验证] 自动测试及页面验收结果见 [acceptance.md](../../evidence/007-learning-foundation/acceptance.md)。有 MongoDB、Playwright 和 Chromium 时，可运行 `python3 -m scripts.acceptance_learning` 重做隔离验收；可用 `BROWSER_EXECUTABLE` 指定浏览器路径。此脚本创建并清理自己的临时数据库，不向实际资料库写样例。

[方案] 打开 /login 登录，从首页“学习工作台”进入 /learning：保存“理解 Redis 持久化”，刷新仍保留；浏览资料不新增已读，主动标记后切换到已读列表，撤销后消失。换另一账户检查隔离。

[方案] 老语料应显示“仅摘要／仅问题，缺少解答”，不自动显示英文作为中文正文。完整正文、部分译文、过期结果、并发标记和未配置模型由自动测试使用明确标注的测试样例验证；这些不代表完成真实全文采集和模型处理。

[方案] 浏览器验证桌面和手机尺寸；执行记录写入 evidence/007-learning-foundation/acceptance.md，标明测试替身与真实数据库检查的边界。
