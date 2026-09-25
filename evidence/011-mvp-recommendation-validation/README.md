# 011 MVP冻结案例与评价

`cases.json`在011生产算法修改前冻结，SHA-256见`cases.sha256`；17个场景、四主题、16条有偏移的原文事实。16篇真实材料来自既有10,215篇背景，另外2篇镜像/改写明确为合成反例，不计入真实来源。`derived-fixtures.json`保存反例的构造依据。事实/标签由代理读原文整理，不冒充独立人类评价。

PostgreSQL/Python为开发场景；Docker/Django为保留验收。保留场景首次运行后即成为已观察案例，不能继续调参后宣称盲测。全部门槛按011 Spec SC-001至SC-006，失败不改输入或降低门槛。

评价程序：`scripts/evaluate_mvp011.py`。

```bash
python3 scripts/evaluate_mvp011.py verify
KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH="$PWD/state/feature010/media/translate-zh_en-1_9" \
SPARK_MASTER='local[1]' \
PYSPARK_SUBMIT_ARGS='--driver-memory 1g --conf spark.sql.files.maxPartitionBytes=16777216 --conf spark.sql.shuffle.partitions=4 pyspark-shell' \
python3 scripts/evaluate_mvp011.py run --mode baseline --split development --pool small \
  --output evidence/011-mvp-recommendation-validation/baseline-dev-small.json
```

`--pool full --scope background`复用009完整万条索引，不通过筛除干扰制造正确结果。`--pool small`仅用于定位；负例显式限制候选池，不能把有限池空结果说成万条库没有答案。中文是实际输入；`--language en`仅诊断，不替代中文验收。

010旧Python包在ignored的`state/feature011/evaluation/baseline-v4/knowpipe`保存，61个文件哈希见`baseline-code-manifest.json`。运行时以独立模块名打包并分发到Spark，避免并行修改的当前代码污染旧UDF。代码、输入和提供器身份变更时不能混用resume结果。

对原始结果运行`review-template --results 文件 --output 审核草稿`，逐条读真实候选与历史正文，填中文理由和原文偏移；审核文件必须绑定结果SHA-256。然后`score --results 文件 --reviews 审核文件 --output 统计文件`。程序拒绝无原文证据的审核，补充通过还需历史侧证据。未标注结果列待复核，参考文档ID命中不自动算相关；即使输出全空、全部拒绝补充，也不能通过正例要求。原文跨度是必要条件，语义正确性仍须明确的来源事实复核。

本文件是流程说明，不是通过报告。真实中文阅读、消融、Web和RSS闭环需另记，不能从检索命中或单测数量推出MVP已通过。

最终组合的完整背景结果为 `current-v6-full.json`，对应正式原文审核、统计和消融分别为 `current-v6-full-review.json`、`current-v6-full-score.json`、`current-v6-full-ablations.json`。17案已完成；其中13案使用10,215篇背景，4案为明确限定的受控场景。`current-v6-full-resource-events.json`记录本机内存不足后的同签名检查点重启，已完成案例没有重跑。

最终组合使用固定Qwen段落v1，旧基线使用原Argos。因此最终新旧比较同时包含算法与语言提供器变化；只有开发小池的旧版/v6 Argos对照可隔离算法因素。`semantic.status=partial`表示部分单元未完成支持，并不说明所有资料错误；真实原文审核也不能将partial改成模型完整执行。最终判定、已观察失败和仍待验证内容见 `traceability.md`。
