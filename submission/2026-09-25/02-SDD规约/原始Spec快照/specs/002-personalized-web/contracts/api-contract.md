# Interface Contract: HTTP API（Flask 路由）

[已验证] 2026-09-16 安全修订：所有修改请求需先用同一 cookie 会话访问 GET /api/auth/csrf，再发送 X-CSRF-Token 请求头；登录后需重新获取 token。旧示例需补充该步骤。生产/新接口契约见 Feature 003。


**Feature**: [spec.md](spec.md) | 端点清单来自《课程项目选题调研与实施方案.md》
第 5.5 节，本文档补充鉴权要求、请求/响应结构与错误契约。

## 鉴权模型

- 会话鉴权：登录成功后由 Flask 写入服务端签名 session cookie（`user_id`）。
- 标记为"需要登录"的端点：请求缺少有效 session 时返回 `401`；session 有效但
  请求参数中的 `user_id` 与 session 中的 `user_id` 不一致时返回 `403`（不允许
  访问他人数据，spec Edge Cases 与 FR-005）。
- 标记为"公开"的端点：不检查 session，任何人可访问（对应 spec FR-009/010，
  主题列表与文档详情不涉及用户个人数据）。

## Contract 1: `POST /api/auth/register`

**鉴权**：公开。

**请求体**：`{"username": string, "password": string}`

**响应**：
- `201`：`{"user_id": string, "username": string}`
- `409`：`username` 已存在 — `{"error": "username_taken"}`
- `400`：字段缺失或密码过短（阈值由实施阶段决定并写入错误信息）

## Contract 2: `POST /api/auth/login`

**鉴权**：公开。

**请求体**：`{"username": string, "password": string}`

**响应**：
- `200`：登录成功，设置 session cookie，返回 `{"user_id": string, "username": string}`
- `401`：用户名不存在或密码错误 — `{"error": "invalid_credentials"}`（不区分
  两种失败原因，避免用户名枚举）

## Contract 3: `POST /api/auth/logout`

**鉴权**：需要登录。

**响应**：`200`，清除 session。

## Contract 4: `GET /api/topics`

**鉴权**：公开。

**响应**：`200` — `{"topics": [{"topic_cluster_id": int, "top_keywords": [string], "document_count": int}]}`，
按 `document_count` 降序排列，供用户在设置已知主题时选择。

## Contract 5: `GET /api/documents/{source}/{doc_id}`

**鉴权**：公开。

**响应**：
- `200` — 文档详情（标题、正文、来源、链接、许可证）+ 关联挖掘结果摘要
  （关键词、主题簇、`reliable` 标记）
- `404`：文档不存在 — `{"error": "not_found"}`

## Contract 6: `GET /api/recommendations`

**鉴权**：需要登录；`user_id` 必须与 session 一致。

**查询参数**：
- `user_id`（必填，string）
- `limit`（可选，int，默认 20，必须为正整数，否则 `400`）
- `source`（可选，`stackexchange` \| `arxiv`，不填则不筛选）
- `mode`（可选，`personalized`（默认）\| `baseline`，`baseline` 返回 research.md
  第 4 节定义的非个性化基线排序，用于效果对比）

**响应**：`200` —
```json
{
  "user_id": "string",
  "mode": "personalized",
  "items": [
    {
      "knowledge_id": "string",
      "status": "new",
      "matched_keywords": ["string"],
      "matched_topic_cluster_id": null,
      "score": 0.0,
      "document": {"source": "string", "doc_id": "string", "title": "string", "source_url": "string"}
    }
  ]
}
```
`mode=baseline` 时 `items` 内每条不含 `status`/`matched_keywords`/
`matched_topic_cluster_id` 字段（基线排序不做个性化判定）。

**错误**：
- `400`：`limit` 非正整数，或 `source`/`mode` 不在允许枚举值内
- `401`/`403`：见"鉴权模型"

## Contract 7: `POST /api/profile/topics`

**鉴权**：需要登录；请求体 `user_id` 必须与 session 一致。

**请求体**：`{"user_id": string, "known_topics": [string]}`（整体替换已知主题
集合，不是增量追加；增量场景由前端在提交前合并好完整列表）

**响应**：
- `200`：`{"known_topics": [string], "updated_at": "ISO8601"}`
- `400`：`known_topics` 内存在无效主题簇标识（不在 `/api/topics` 返回范围内）

**副作用**：更新 `UserProfile.known_topics`/`known_keywords`，触发该用户名下
`user_knowledge` 记录的重新判定（同步完成，响应返回前完成，见 research.md
第 5 节）。

## Contract 8: `POST /api/profile/feedback`

**鉴权**：需要登录；请求体 `user_id` 必须与 session 一致。

**请求体**：`{"user_id": string, "knowledge_id": string, "action": "confirmed_known" | "useful" | "irrelevant"}`

**响应**：
- `200`：`{"knowledge_id": string, "action": string, "created_at": "ISO8601"}`
- `400`：`action` 不在允许枚举值内，或 `knowledge_id` 格式不合法

**副作用**：追加一条 `UserProfile.feedback_history` 记录；`action =
confirmed_known` 时额外将对应关键词/主题簇纳入 `known_keywords`/
`known_topics`，触发相关知识单元重新判定（对应 spec FR-009/017/019 与
User Story 3）。

## 通用错误契约

所有端点的错误响应统一为 `{"error": "<机器可读错误码>"}` 结构，不在响应体中
暴露堆栈信息或内部实现细节；页面层负责把错误码转换为面向用户的提示文案。
