# Validation

[方案] 安装requirements.txt、Java和MongoDB；设置SPARK_LOCAL_IP=127.0.0.1、SPARK_MASTER=local[2]。参数详见contracts/interfaces.md。

1. 使用scale collect冻结真实数据；scale run输出实际计数和事件日志；再次使用同一快照运行，确认documents数量不增长。
2. study prepare产生manifest.json及label.html；真人打开页面逐项标注并下载labels.json；study evaluate计算指标。未提供真实标签时保持验收待完成。
3. 将真实ready节目复制到实验库，执行linking.job。订阅用户打开节目，检查关联词/文献详情；未订阅用户404；修改内容后旧关联消失。
4. `SPARK_LOCAL_IP=127.0.0.1 python3 -m unittest discover -v`；`node --check knowpipe/web/static/app.js`。

[方案] 原始语料和评审信息保留state/，公开证据仅含去身份统计。测试替身与真实数据库运行分开记录。

## 本轮真实数据的复跑命令

[已验证] 冻结数据位于本机ignored `state/course-006/corpus.jsonl`，SHA与计数见evidence；原演示库保持独立。

```bash
export SPARK_LOCAL_IP=127.0.0.1 SPARK_MASTER='local[2]'
python3 -m knowpipe.mining.scale run --mongo-db knowpipe_course_006 --snapshot state/course-006/corpus.jsonl --output state/course-006/scale-new.json
python3 -m knowpipe.evaluation.study prepare --mongo-db knowpipe_course_006 --profiles state/course-006/profiles.json --output state/course-006/study-new
# 真人打开study-new/label.html，逐篇评分后下载labels.json：
python3 -m knowpipe.evaluation.study evaluate --manifest state/course-006/study-new/manifest.json --labels state/course-006/study-new/labels.json --output state/course-006/evaluation.json
python3 -m knowpipe.linking.job --mongo-db knowpipe_course_006 --output state/course-006/linking-new.json
python3 scripts/acceptance_course.py --mongo-db knowpipe_course_006 --output state/course-006/linking-acceptance.json
```

[方案] 新机器先启动MongoDB，运行scale collect从公开API冻结新输入（需要已有非空arXiv补充源，可先运行README的两来源切片命令）。原始内容在state/，不会随Git推送。浏览器验收另需Playwright及Chromium，可设置BROWSER_EXECUTABLE。单机运行各Spark作业请顺序执行，避免资源争用。
