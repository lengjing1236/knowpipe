# Quickstart: 个性化知识分类与 Web 展示

**Feature**: [spec.md](spec.md) | 字段/契约细节见 [data-model.md](data-model.md)、
[contracts/api-contract.md](contracts/api-contract.md)

本指南给出验证本 feature 是否达成 User Story 1（最小垂直切片）、User Story 2
（规模化判定与效果评价）、User Story 3（反馈闭环）的可执行步骤，不包含完整
实现代码。

## 前置条件

1. Feature 1 已完成 P1（最小链路），`documents`/`mining_results` 中存在两来源
   各 100 条对应的记录，且都携带同一个 `batch_id`（参见
   [Feature 1 quickstart Step 1](../001-data-pipeline-spark-mining/quickstart.md)）。
2. MongoDB 实例可连接（复用 Feature 1 research.md §1 已验证的本机实例或课程
   提供的实例），本 feature 在其中新增 `users`/`user_profiles`/`user_knowledge`
   集合，不影响 Feature 1 已有集合。
3. Flask 应用可启动：`python3 -m knowpipe.web.app`（具体命令行接口由 tasks.md
   决定，此处为契约层示例），默认监听本机端口。

## Step 1: 打通最小垂直切片（对应 User Story 1）

```bash
# 1. 注册并登录一个测试账户
curl -s -X POST http://127.0.0.1:5000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "password": "demo-pass-1234"}'

curl -s -c cookies.txt -X POST http://127.0.0.1:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "password": "demo-pass-1234"}'

# 2. 查看可选主题（来自 Feature 1 切片数据的主题簇）
curl -s http://127.0.0.1:5000/api/topics

# 3. 设置已知主题（取上一步返回的某个 topic_cluster_id）
curl -s -b cookies.txt -X POST http://127.0.0.1:5000/api/profile/topics \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<注册返回的 user_id>", "known_topics": ["<topic_cluster_id>"]}'

# 4. 请求推荐结果
curl -s -b cookies.txt \
  "http://127.0.0.1:5000/api/recommendations?user_id=<user_id>&limit=10"
```

**预期结果**（对应 spec SC-001）：
- `/api/topics` 返回的主题数量与 Feature 1 切片数据的 `topic_cluster_id` 去重
  数一致。
- `/api/recommendations` 返回的每条 `items[]` 都带有非空 `status`（四态之一）
  与判定依据（`matched_keywords` 或 `matched_topic_cluster_id` 至少一项非空，
  或在 `status = new` 时二者均可为空表示确实未命中）。
- 命中已设置的已知主题簇的知识单元，`status` 为 `known` 或 `refine`，不是
  `new`。

## Step 2: 校验新用户默认全新（对应 Edge Cases / FR-013）

```bash
curl -s -X POST http://127.0.0.1:5000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "fresh", "password": "demo-pass-1234"}'
# 登录后直接请求推荐，不设置任何已知主题
```

**预期结果**：全部返回条目的 `status` 均为 `new`，请求本身正常返回 `200`，
不报错、不返回空列表（除非切片数据本身为空）。

## Step 3: 校验越权与鉴权拒绝（对应 FR-004/005 与 Edge Cases）

```bash
# 未登录直接请求推荐
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:5000/api/recommendations?user_id=<user_id>&limit=10"
# 预期 401

# 已登录但请求他人 user_id
curl -s -b cookies.txt -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:5000/api/recommendations?user_id=<别人的 user_id>&limit=10"
# 预期 403
```

## Step 4: 反馈闭环（对应 User Story 3）

```bash
# 对某条被判定为 new 的 knowledge_id 提交"已掌握"反馈
curl -s -b cookies.txt -X POST http://127.0.0.1:5000/api/profile/feedback \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<user_id>", "knowledge_id": "<knowledge_id>", "action": "confirmed_known"}'

# 重新请求推荐结果
curl -s -b cookies.txt \
  "http://127.0.0.1:5000/api/recommendations?user_id=<user_id>&limit=10"
```

**预期结果**（对应 SC-004）：该 `knowledge_id` 及关联同一关键词/主题簇的其他
条目，`status` 从 `new` 变为 `known` 或 `refine`。

## Step 5: 规模化判定与基线对比（对应 User Story 2）

前提：Feature 1 已完成规模化运行（≥10,000 条），`documents`/`mining_results`
已是全量数据。

```bash
# 触发全量推荐分数计算（Spark 作业）
python3 -m knowpipe.web.score_job --mongo-uri "<连接串>" --mongo-db knowpipe_mining

# 对至少两个模拟用户画像分别请求个性化与基线两种模式
curl -s -b cookies.txt \
  "http://127.0.0.1:5000/api/recommendations?user_id=<user_id_A>&limit=20&mode=personalized"
curl -s -b cookies.txt \
  "http://127.0.0.1:5000/api/recommendations?user_id=<user_id_A>&limit=20&mode=baseline"
```

**预期结果**（对应 SC-002/SC-003）：
- `score_job.py` 输出的 `batch_id` 可在 `batches` 集合中查到起止时间和统计
  （复用 Feature 1 的批次证据查询方式）。
- 对每个模拟用户画像，人工比对 `mode=personalized` 与 `mode=baseline` 两组
  结果的相关性标注，计算 Precision@K 或等价指标，得到可比较的两组数值。

## 已知限制

- 本 quickstart 使用 `curl` 演示 API 契约，不覆盖单页 Web 的界面交互细节
  （页面布局、图表渲染），页面本身的可用性通过浏览器手动验证并录屏留证。
- `possible_conflict` 状态的人工复核流程（LLM 生成候选 → 复核 →
  `pending_review` 置 false）需要真实的冲突场景数据才能演示，不保证在
  100+100 条最小切片规模下必然出现，若切片数据中未触发该场景，需在规模化
  阶段（Step 5）或专门构造的测试数据中补充验证。
