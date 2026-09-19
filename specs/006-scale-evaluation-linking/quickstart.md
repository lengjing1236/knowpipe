# Validation

[方案] 安装requirements.txt、Java和MongoDB；设置SPARK_LOCAL_IP=127.0.0.1、SPARK_MASTER=local[2]。参数详见contracts/interfaces.md。

1. 使用scale collect冻结真实数据；scale run输出实际计数和事件日志；再次使用同一快照运行，确认documents数量不增长。
2. study prepare产生manifest.json及label.html；真人打开页面逐项标注并下载labels.json；study evaluate计算指标。未提供真实标签时保持验收待完成。
3. 将真实ready节目复制到实验库，执行linking.job。订阅用户打开节目，检查关联词/文献详情；未订阅用户404；修改内容后旧关联消失。
4. `SPARK_LOCAL_IP=127.0.0.1 python3 -m unittest discover -v`；`node --check knowpipe/web/static/app.js`。

[方案] 原始语料和评审信息保留state/，公开证据仅含去身份统计。测试替身与真实数据库运行分开记录。
