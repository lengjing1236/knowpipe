# 固定双语开发场景结果

[已验证：实际模型与实际Spark] 语料沿用009的10,215篇全文、222,579段、5个来源；原文与机器译文共同查找。英文参考目标单独计算，未替换错误译文。本表不是独立相关性标注。

| 场景 | 中文条数/首项 | 英文条数/首项 |
| --- | --- | --- |
| db-1 | 10 / 13.2. Transaction Isolation # | 10 / 13.2. Transaction Isolation # |
| db-2 | 10 / What index can be created to optimise this query? | 10 / Can PostgreSQL use an index when the first column in the index is not used |
| lang-1 | 10 / 8. 错误和异常 | 10 / 8. 错误和异常 |
| lang-2 | 10 / 协程与任务 | 10 / 协程与任务 |
| web-1 | 10 / 数据库事务 | 10 / 数据库事务 |
| web-2 | 3 / 异步支持 | 0 / no_object_candidates |
| ops-1 | 8 / Bind mounts | 10 / Volumes |
| ops-2 | 10 / How to Design a Secure Script for Conditional File Access Based on Time and API Conditions? | 10 / How to Design a Secure Script for Conditional File Access Based on Time and API Conditions? |

初步复核：数据库隔离、Python异常/协程、Django事务出现直接对应的文档；跨站请求伪造的中文首项为异步支持、英文为空，操作系统调度的中文首项为文件访问脚本，均不能作为该学习目标已满足的证据。计数非零不等于相关，英文参考也不是完美标签。

必要后续：更可靠的技术目标翻译、语义召回对照、具体主题全文补充，以及独立材料级相关性验证。原始译文错误保存在goal-interpretations.json，不为追求成功改写目标。

当前原文依据有时只命中章节标题；阶段C应把标题与相邻正文组织成可读证据单元，避免短标题高分代替实质讲解。
