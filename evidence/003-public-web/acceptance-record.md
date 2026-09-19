# Feature 003 验收记录

日期：2026-09-16。

## 答辩前补充：无需服务器/域名的临时 HTTPS

- [已验证] `scripts/defense_demo.py` 管理真实本机 MongoDB、两个 Gunicorn 入口、Spark worker 和可选 Cloudflare Quick Tunnel。数据保存在被忽略的 `state/defense/`，MongoDB/Web 原始端口仅绑定回环地址。
- [已验证] 完成真实节目处理后停止所有演示服务，再以 `start --public` 启动。两个节目批次仍为 `20260916-7757495c` / `20260916-017c85ae`，总计 3 集与 2 条目标通知，未重新计算或丢失数据。
- [已验证] 本次临时地址为 `https://hundred-belfast-blackberry-females.trycloudflare.com`。从本环境经公网 HTTPS 注册/登录/CSRF/详情/通知/健康检查通过，Cookie 为 Secure，两个用户隔离通过：[HTTP 证据](../004-podcast-insights/real-public-https.json)。
- [已验证] 实际 HTTPS 浏览器登录、真实文字稿、182 段分析结果中的 React 88 段、桌面/390px 布局、退出通过；另通过阻断 SSE 验证真实持久通知轮询：[浏览器日志](../004-podcast-insights/defense-browser-public.log)。JavaScript 异常 0。
- [已验证] [公网桌面截图](../004-podcast-insights/public-real-podcast-desktop.png)、[公网文字稿截图](../004-podcast-insights/public-real-podcast-transcript.png)、[公网手机尺寸截图](../004-podcast-insights/public-real-podcast-mobile.png)。手机尺寸截图不是手机移动网络验证。
- [已验证] 最新完整回归 99 项通过：[defense-final-tests.log](../005-defense-evidence/defense-final-tests.log)。

[待确认] 临时隧道必须保持电脑和服务运行，未取得永久云托管；手机移动网络独立访问和公网负载测试尚未完成。Cloudflare Quick Tunnel 不支持 SSE，本次新增每 5 秒读取持久通知的兼容方式。

## 已验证

- [已验证] 先编写 6 个安全/身份测试，原实现全部报错（缺少端点）：[tests-red.log](tests-red.log)。实现后通过，最后修订覆盖 UTC 时间标记：[security-final.log](security-final.log)。
- [已验证] 完整测试套件 97 项通过，包含真实 Spark；[完整日志](../005-defense-evidence/full-tests.log)。额外针对 UTC 展示修订重跑安全测试通过。
- [已验证] 真实 MongoDB 7.0.14（独立目录 /tmp/knowpipe-acceptance-mongo、回环端口 27028）+ Gunicorn 23.0.0 两进程真实 HTTP：CSRF、登录/会话身份、节目分析详情、SSE、未登录 401、readiness 均通过：[real-http.log](real-http.log)。
- [已验证] Chromium + Playwright：登录后不依赖 URL user_id；身份、SSE、完整文字稿、分析片段、登出；1440px 桌面与 390px 手机无横向溢出；浏览器 JavaScript 异常为 0：[browser.log](browser.log)。
- [已验证] [桌面截图](browser-desktop.png)、[手机截图](browser-mobile.png)；界面内容来自明确标注的合成验收数据，文献数量为 0，未伪造规模。
- [已验证] node --check 与 git diff --check 通过。Compose YAML 可解析，仅 Caddy 映射公网端口，数据库仅内部网络。

## 尚未验证

- [待确认] 本环境无 Docker，未执行镜像构建/Compose 启动；YAML 检查不能替代容器启动验收。
- [待确认] 用户无自有服务器/域名；已补充上述临时 HTTPS 与本机重启持久化验收，永久云部署及手机独立网络访问仍未验证。
- [待确认] 未进行公网负载测试；SSE 使用线程，当前配置定位课程小规模展示。

## 重跑

测试命令见 Feature 003 quickstart。独立库端到端数据由 scripts/acceptance_podcast.py 生成（需自行启动回环端口 27028 的 MongoDB），/tmp/knowpipe-acceptance.json 记录数据库名；启动 Gunicorn 到 127.0.0.1:8018 并指向该库后，运行 scripts/acceptance_http.py。

浏览器验收另需 playwright 与 Chromium；可用 pip install playwright、python3 -m playwright install chromium 安装，或以 BROWSER_EXECUTABLE 指向已有 Chromium，再运行 scripts/acceptance_browser.py。它不是生产运行依赖。
