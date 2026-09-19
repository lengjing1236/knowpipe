# 本项目的规约驱动开发流程

[已验证] 项目已有 001/002 规约与本地 .specify 宪法；本次将宪法纳入版本控制可见范围，避免治理要求只留在开发机。

每次修改按以下顺序进行：

1. 用户故事与验收边界写入 spec.md，区分课程要求、已验证、方案、待确认。
2. 用 codegraph 查询上下文/调用影响，再核对源文件；工具不支持的结果不能当作事实。
3. plan.md 记录模块边界、技术决策和宪法检查；涉及 API/存储时先更新契约。
4. tasks.md 明确依赖；对身份、网络、幂等、Spark 和评价等实质功能先写验收测试，留存失败结果。
5. 实现并执行相应测试；只有发现新变更/失败/未决风险才扩展验证。
6. evidence/ 写清输入、环境、真实组件/替身、命令与结果；根据真实证据更新任务勾选。
7. 外部部署和业务验收单独确认，配置文件/测试通过不等于已经上线或推荐质量提升。

本次需求原文：

> 请注意，使用codegraph分析当前项目，然后，我需要将这个项目优化至足以进行课程答辩的程度，其中的Web项目不能只在单机运行，需要能上架互联网，被其他用户访问，底层功能方面，由于我不懂大数据分析，Spark，所以需要你帮我继续思考新的功能点，可以询问我需求，有一点功能可以支持，订阅播客并实时推送播客文字稿的大数据处理产物，全程使用规约驱动开发

[已验证] 据此新增 003（公网）、004（播客）、005（答辩证据）三个工作单元；具体功能边界在各自规约里。未收到回复的问题是部署资源、答辩日期/集群要求、目标播客和推送偏好；独立工作按低成本部署方案、站内通知、官方/自有文字稿先行，尚未代用户采购服务或发送邮件。

## 在 Codex 中使用 Spec Kit

[已验证] 2026-09-16，本机 `specify 1.0.4` 已为本仓库安装 Codex 接入，并将其设为默认；原有 Claude 接入、项目宪法和 001–005 规约均保留。执行的是适用于已初始化项目的增量接入：

```bash
cd /home/lengjing1236/knowpipe
specify integration install codex
specify integration use codex
specify integration status
```

状态检查结果为 `OK`，默认接入为 `codex`，已安装接入为 `claude, codex`，缺失或被修改的受管理文件均为 0。

对尚未初始化的项目，在项目根目录执行：

```bash
specify init --here --force --integration codex --non-interactive
```

`--here` 表示在当前目录初始化；`--force` 允许向非空目录合并并覆盖模板；`--non-interactive` 避免脚本运行时等待交互选择。已初始化的项目优先使用上面的增量接入命令。当前版本默认安装 Skills，无需额外传入 `--integration-options="--skills"`。

### 聊天中的操作顺序

在 VS Code 的 Codex 扩展中打开本项目并新建会话；CLI 用户在项目根目录运行 `codex`。新技能位于 `.agents/skills/speckit-*/SKILL.md`，可以在聊天输入框键入 `$speckit-` 选择。若当前会话尚未发现新技能，重开会话；必要时重载 VS Code 窗口。

下面每一行都是发送给 Codex 的聊天消息，按阶段分别发送；`$` 是技能调用前缀，不是终端提示符。示例仅用于说明用法，尚未创建这个需求。

```text
$speckit-specify 为播客分析结果增加关键词检索，用户可以按节目筛选并跳转到原文段落。先编写用户故事、功能边界和可验证的验收标准。
$speckit-clarify
$speckit-plan 沿用 Flask、MongoDB 和现有 Spark 处理结果，复用鉴权与数据契约，遵循项目宪法。
$speckit-tasks
$speckit-analyze
$speckit-implement
```

| 技能 | 用途与主要产物 |
|---|---|
| `$speckit-constitution` | 建立或修订 `.specify/memory/constitution.md`；本项目已有正式宪法，按需修订 |
| `$speckit-specify` | 编写需求与验收标准，生成 `specs/NNN-功能名/spec.md` |
| `$speckit-clarify` | 澄清需求歧义，补充 `spec.md` |
| `$speckit-plan` | 编写技术计划、研究、数据模型、契约及验证指南 |
| `$speckit-tasks` | 将计划拆成有依赖关系的 `tasks.md` |
| `$speckit-analyze` | 检查需求、计划与任务之间的一致性，实施前处理发现的问题 |
| `$speckit-implement` | 按任务实现与验证，并依据实际结果更新完成状态 |
| `$speckit-checklist` | 按需生成需求质量检查清单 |

### 继续已有功能

[已验证] 接入时 `.specify/feature.json` 仍指向 `specs/002-personalized-web`。继续 003、004 或 005 时，应明确目标目录；仅提及功能名称或切换 Git 分支，不足以保证脚本选中目标。

例如，在 Codex 聊天中发送：

```text
继续现有播客功能，目标目录为 specs/004-podcast-insights。
将 Spec Kit 当前 feature_directory 设置为该目录，读取已有 spec.md、plan.md、tasks.md 和项目宪法，然后执行 $speckit-analyze。
```

CLI 也可在启动前指定目录：

```bash
SPECIFY_FEATURE_DIRECTORY=specs/004-podcast-insights codex
```

修改已有功能时，先对齐已有规约、计划和任务，再进入实现；新功能再从 `$speckit-specify` 开始。

[已验证] 当前 `.gitignore` 忽略 `.agents/` 和大部分 `.specify/` 文件，因此本次接入是当前工作区配置；在另一台机器克隆后，需要安装 Specify CLI 并重新执行初始化。项目宪法和 `specs/` 没有被这些规则忽略。

本次用法依据本机 CLI 帮助、Codex 接入实现、生成的技能文件和 `specify integration status` 核验。OpenAI 官方 Skills 文档地址为 <https://developers.openai.com/codex/skills/>；本次访问返回 HTTP 403，未作为已读取的验证来源。
