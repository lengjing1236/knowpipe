# 公网部署与验证

[方案] 2026-09-17 答辩不需要先购买服务器或域名：已补充本机启动脚本、可选 Cloudflare Quick Tunnel、通知轮询兼容和真实编程播客验收。操作见 [答辩运行手册](../../defense/明日答辩运行手册.md)。以下 VPS/Caddy 方案保留用于长期部署。

[方案] 推荐起点：有公网 IPv4 的 Linux VPS、2–4 vCPU、至少 8 GB 内存、20 GB 可用磁盘；域名 A/AAAA 指向服务器。资源值是起点，不是压测结论。Web 可独立部署到较小实例，Spark worker 放到计算资源更充足的主机，二者连接同一受保护的 MongoDB。

## 部署

服务器安装 Docker Engine 与 Compose v2，在项目根目录执行：

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

分别把两个输出写入 .env 的 SECRET_KEY 与 MONGO_PASSWORD；设置自己的 DOMAIN（仅域名，不带 https://）。打开服务器入站 TCP 80/443；不要开放 27017、8000 或 Spark 管理端口。

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 web worker caddy
curl --fail https://你的域名/health/ready
```

Caddy 自动申请 HTTPS 证书。浏览器访问 https://你的域名/login，注册、登录、订阅 RSS。后台每 5 分钟检查更新，SSE 显示完成通知；默认不自动下载/转写音频。

容器的 Web worker 为 2 × 16 threads。SSE 会占线程，首版适用于小规模课程展示；超过约二十位同时在线用户需实测后调整线程/部署事件服务，不能直接承诺高并发。RSS 请求不在 Web 请求路径中，添加订阅仅验证公共 DNS。

## 导入课程文献

保持 Web 可用，单独运行计算容器（需要来源 API 配额和足够资源）：

```bash
docker compose run --rm worker sh -c 'python -m knowpipe.mining.spark_job --sources stackexchange arxiv --target-count 100 --source-target-count stackexchange=10500 arxiv=500 --mongo-uri "$MONGO_URI"'
docker compose run --rm worker sh -c 'python -m knowpipe.web.score_job --mongo-uri "$MONGO_URI" --mongo-db "$MONGO_DB"'
```

目标采集条数不等于有效条数；必须检查实际 Stack Exchange 有效文档 ≥10,000。先按 Feature 001 quickstart 完成 100+100 切片，再扩大数据量，遵守来源配额。

## 运维与验收

- 从另一网络/手机移动网络访问公开 HTTPS；分别用两个账户验证订阅隔离。
- 重启：docker compose restart；正常重建镜像/容器保留命名卷。不要使用 down -v，后者删除持久数据。
- 备份：在服务器私有目录保存 Mongo dump，避免提交到仓库；示例：

```bash
mkdir -p backups
chmod 700 backups
docker compose exec -T mongo sh -c 'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive --gzip' > backups/knowpipe.archive.gz
chmod 600 backups/knowpipe.archive.gz
```

- 恢复前停止 web/worker，在隔离库验证备份，再按需要用 mongorestore --archive --gzip 恢复；恢复生产数据前确认目标库与备份时间。
- 日志查到 failed 不代表可忽略；检查 sources、有效数、失败数和 batch_id。
- SECRET_KEY 变更会使现有登录失效。Mongo 初始化密码仅首次空卷生效，已有卷改密码需要在数据库里执行用户更新。
- 当前 Compose 使用数据库管理用户以减少课程部署步骤；长期运营应创建仅限业务库的账户并替换 MONGO_URI。

[已验证] 用户没有服务器和域名，本次以 Cloudflare 临时 HTTPS 完成实际公网 HTTP/浏览器验收，见 Feature 003 最新记录；同时重启本机进程验证节目和批次保留。[待确认] VPS/Caddy 长期部署、手机独立网络和公网负载测试仍未完成。
