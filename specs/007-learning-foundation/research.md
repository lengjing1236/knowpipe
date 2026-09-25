# 研究与决策

[已验证] 依据根目录规划、现有代码和专门的只读研究；2026-09-23。

## 个人状态

- **Decision**: 扩展 user_profiles.learning_goal、read_doc_ids、learning_revision；已读引用增加 read_at、content_version。
- **Rationale**: 旧 score_job / baseline 只读取 source/doc_id，可兼容；单文档原子条件更新不需要副本集事务。
- **Alternatives considered**: 独立历史集合再双写旧画像会引入不一致，当前没有事务部署；整份覆盖画像会丢失并发变更。
- **已验证风险**: routes_api.update_topics 原先将 read_doc_ids 派生为 known_keywords，需移除这条输入。画像首次创建需处理唯一键并发冲突。

## 正文与处理状态

- **Decision**: 以 language + 原文文本的 SHA-256 作为内容版本；仅显式通过完整正文发布函数的数据具有 fulltext 状态。历史 arXiv 视为摘要、Stack Exchange 视为仅问题，其余为未验证。
- **Rationale**: 入库成功、文本长短和非空均不能证明全文完整。无版本或版本不符的译文及分析不能继续作为当前产物。
- **Alternatives considered**: 自动将旧 body_text 视为全文，会违反已确认的阅读要求；仅按 doc_id 缓存翻译会展示过期内容。
- **边界**: 版本指纹不是全文质量自动判定；发布者／解析器需要声明已完成提取，完整性质量验收仍属后续采集任务。旧正文快照暂不保存，版本变化后仅提示重读，不做虚假历史对照。

## 运行与页面

- **Decision**: 单独 /learning 页面和 /api/learning 接口，所有资料标识走 query/JSON，避免旧 arXiv ID 的斜线问题。
- **Rationale**: 当前首页以播客与关键词为主，保留兼容入口，新页面不展示“已掌握／未知知识”推断。
- **Alternatives considered**: 一次性重写旧页面与推荐会扩大未确认范围；另建前端框架没有必要。
- **已验证**: 当前公开 documents 不含私人资料；podcast_episodes 有订阅可见性，不能通过公共资料端点直接暴露。本阶段仅接公共 documents，播客接入留给后续统一权限适配。

## 模型依赖

- **Decision**: 转写和翻译定义可替换接口；缺少提供方时返回明确不可用，不产生模拟译文。
- **Rationale**: 服务和费用未确认，不妨碍状态、缓存和版本保护的实现。
- **Alternatives considered**: 默认付费 API 或安装大模型都不作为假设。自动转写、下载音频和真实服务验收仍待后续阶段。

[已验证] .specify/extensions.yml 不存在，本轮各阶段无扩展 hooks。现有脚本没有执行权限，使用 bash 显式运行，不修改权限。
