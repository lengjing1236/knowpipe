# Feature 003 实施计划

[方案] 保留 Flask/Jinja/原生 JS/MongoDB，避免引入第二套前端和存储。

1. 先修复身份初始化与公开输入边界，新增 security 模块负责 CSRF、生产限流和响应头。
2. 新增状态 API、页面和样式，用户身份从 session 查询。
3. Web 镜像只安装 Web 依赖；worker 镜像增加 Java/PySpark。Caddy 自动签发 TLS。
4. 先运行自动化用例与 WSGI 冒烟，再按 quickstart 在用户提供的环境部署。

宪法检查：[方案] 不改变核心计算边界，不伪造数据规模/评价，不修改知识状态定义。
codegraph 编辑上下文已覆盖 create_app，调用/依赖分析已覆盖 run_mining、routes_api。
