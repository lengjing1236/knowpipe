# Research
[已验证] 当前资料规模已达标，但009发生中文进程调度无结果、PostgreSQL目标首推Spring；媒体存在意义误译；机器只有一台。

- **Decision**: 使用真实中→英目标转换并保留原语言分支。**Rationale**: 比继续硬编码中英术语表覆盖更广，同时评分仍由Spark执行。**Alternatives**: 多语言embedding有价值但新增模型/索引/评价成本，本轮不把未测语义向量直接替换核心算法。免费Argos官方目录有zh_en1.9：[目录](https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json)。
- **Decision**: 保留技术产品对象约束。**Rationale**: 同为事务问题不一定满足指定数据库目标。**Alternatives**: 仅调相似度或MMR权重不能稳定排除对象错误。
- **Decision**: 翻译处理器身份、数字/代码完整性与明确质量边界。**Rationale**: 目前完整生成不代表译对，模型升级也需失效旧缓存。**Alternatives**: 仅加警告无法发现确定性缺陷；仅用回译或模型自评分不能作为可靠人工质量标签。
- **Decision**: Standalone client模式、同路径共享挂载作为可落地部署契约。**Rationale**: 当前index.py大量使用Path/本地原子manifest，需要共享可见路径；Python Standalone不支持cluster部署模式。[官方提交说明](https://spark.apache.org/docs/latest/submitting-applications.html)。
- **Decision**: 本机两个独立executor验证进程边界和结果，不宣称多主机。**Rationale**: 用户仅有一台电脑，机器增多不是推荐更准的因果证据。[官方集群架构](https://spark.apache.org/docs/latest/cluster-overview.html)、[调优说明](https://spark.apache.org/docs/latest/tuning.html)。
