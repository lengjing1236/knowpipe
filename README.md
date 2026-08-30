# Knowpipe — 知识精炼管道 MVP

输入任意内容（文章 URL / 文本 / 本地文件 / 视频逐字稿），**只输出你未知的新知识**，
并沉淀为可复习的原子知识卡。设计依据见 [DESIGN.md](DESIGN.md)（第一性原理拆解 + 前人经验调研）。

```
信息源(URL/文本/文件/逐字稿) → [摄入清洗] → [切块] → [LLM 抽取原子卡]
                                          → [候选召回(向量粗筛) + LLM 语义差分: new/known/refine/contradict]
                                          → [记忆库更新] → [新知识报告 + 问答卡建议]
                                          → [review 评分反馈] → 记忆库校准（过滤越来越准）
```

## 快速开始

```bash
cd knowpipe

# 1) 给记忆库播种（代表"你已经知道的内容"；格式见 demo/seed_cards.jsonl）
python3 -m knowpipe seed --file demo/seed_cards.jsonl

# 2) 处理一篇内容，输出新知识报告（三种大脑任选）
python3 -m knowpipe process --url https://example.com/article --out report.md --brain auto
python3 -m knowpipe process --file article.txt --brain heuristic          # 零依赖离线兜底
#    配好 LLM API 后（环境变量见下），--brain auto 会用真实 LLM 做抽取与语义差分

# 2b+) 文章级模式（推荐技术视频）：ASR清洗 + 知识总结文章，不抽碎卡
python3 -m knowpipe process --bilibili BV1ST4y1m7No --p 1 --mode article --out report.md --brain openai
#    --mode article：一次 LLM 调用完成术语纠错与知识总结（不输出逐字稿）
#    文章含：按逻辑分段、专业名词补解释、断裂逻辑补串联、核心要点总结

# 2b) B站：给出 BV 号 → 自动取逐字稿 → 进管道
python3 -m knowpipe process --bilibili BV1DfrdByE2H --p 1 --out report.md --brain auto
#    --bilibili 优先取官方字幕；无字幕时自动降级：飞书妙记（开发环境）→ 本地whisper（需装依赖）
#    可加 --bili-transcriber whisper 强制用本地whisper；--whisper-model medium 指定模型大小
#
# 【本地部署 whisper ASR（推荐，完全离线）】
#   pip install yt-dlp faster-whisper
#   sudo apt install ffmpeg        # WSL/Ubuntu；macOS: brew install ffmpeg
#   首次运行会自动下载 whisper 模型（small约500MB，medium约1.5GB）
#   之后 --bilibili BV 一条命令即可，无需任何云服务

# 2c) 合集批量：循环每个分P，共享记忆库去重，输出汇总报告
python3 -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages 1-3 --out batch.md --brain auto
python3 -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages all --out all.md --brain auto
#    批量任务可加 --bili-resume：每完成一个P就保存检查点，中断后重跑只处理未完成的P
#    --bili-pages 支持 '1-3,5' 或 'all'；每页逐字稿缓存到 cache/bili_transcripts/，重跑不重复ASR
#    共享同一记忆库：后处理的P会自动过滤前面P已写入的新知识（跨P去重）
#    某P取稿失败自动跳过，不影响其他P；最终输出一份含逐P统计的汇总报告

# 3) 查看记忆库 / 给新卡评分（反馈回路）
python3 -m knowpipe status
python3 -m knowpipe review        # 交互评分：k=早已知  l=学到新东西  s=跳过  x=删除
python3 -m knowpipe review --apply review.jsonl   # 非交互评分
```

## 命令

| 命令 | 作用 |
|---|---|
| `seed --file X.jsonl` | 导入种子知识卡（用户已知内容），每行 `{"claim":..., "topic":..., "certainty":...}` |
| `process --url/--text/--text-file/--file ...` | 摄入并处理，输出新知识报告 |
| `process --bilibili BV [--p N]` | B站 BV 号 → 逐字稿 → 进管道（单P） |
| `process --bilibili BV --bili-pages 1-3/all` | 合集批量：循环分P，共享记忆库去重，输出汇总报告 |
| `status` | 查看记忆库状态 |
| `review [--apply F]` | 给 new/refined/conflict 卡评分，回写记忆库 |

`process` 主要参数：`--out`（报告路径，默认 stdout）、`--title`、`--brain`（auto/openai/manual/heuristic）、`--mode`（cards=原子卡模式 / article=文章级模式，默认cards）、`--manual-dir`（manual 模式判定文件目录）、`--memory`（记忆库路径，默认 `memory/cards.jsonl`）；B站相关：`--p`（分P页码，默认1）、`--bili-pages`（合集批量范围，如 '1-3,5' 或 'all'）、`--bili-resume`（合集断点续跑）、`--bili-cache-dir`（逐字稿缓存目录，默认 cache/bili_transcripts）、`--bili-transcriber`（auto/subtitle/lark/whisper）、`--whisper-model`（whisper模型大小）、`--bili-workdir`（妙记产物目录，默认 ./lark_out）。文章级报告仅保留知识总结与元数据，不包含 ASR 原稿或清洗稿。

OpenAI 模式下，长文本分块抽取默认并发 4 路，并与全文摘要并行；如遇网关限流，可设置 `KNOWPIPE_DECOMPOSE_WORKERS=1`，或按需调高该值。
LLM 请求遇到网络错误或限流时默认最多重试 2 次（指数退避），可用 `KNOWPIPE_LLM_RETRIES=0` 关闭重试。

## 大脑（Brain）三种模式

| provider | 说明 |
|---|---|
| `auto` | 有 API key → openai，否则 heuristic。**日常推荐** |
| `openai` | 真实 LLM 做"抽原子卡 + 语义差分"。环境变量：`KNOWPIPE_OPENAI_BASE_URL` / `KNOWPIPE_OPENAI_API_KEY` / `KNOWPIPE_OPENAI_MODEL`（OpenAI 兼容接口） |
| `manual` | 从 `--manual-dir/<input_id>.{decompose.jsonl,classify.jsonl,summary.txt}` 读取预生成判定。用于"用别的大脑（如本 Agent）先想好判定，再跑管道"的演示 / 复现 |
| `heuristic` | 零依赖启发式（句子切分 + TF-IDF 相似度阈值），开箱即跑，质量较低，适合没 key 时兜底 |

记忆库是 `memory/cards.jsonl`（JSONL，原子卡带状态机 + 溯源 + 关联 id）。重复摄入相同内容幂等（不会重复入库）。

## 与你的原始设想的对应

- **B站视频**：`--bilibili BV` 一条命令即可：官方字幕优先，无字幕自动走飞书妙记语音识别（ASR），
  得到逐字稿后进同一条管道；也可手动把任意逐字稿存成文本走 `--file/--text-file`。
- **逐字稿清洗（删口误/润色）** = 由"抽原子卡"这一步完成：LLM 用自己的话复述，天然去掉口误与冗余。
- **"去掉已理解内容"** = 这里的语义差分（known 卡被过滤、不重复推）。
- **"只推出未知的新知识"** = 报告第 1 节（new 卡按主题聚类）+ 问答卡建议（主动回忆）。
- **操作层（上传/下单等）**：本期明确不做，是下一阶段（可逆操作自动、不可逆操作人工确认）。

## 演示（含判定文件的完整示例）

`demo/` 下有一套可复现的演示：
- `seed_cards.jsonl` — 10 张种子卡（代表已知知识）；
- `report_personalai.md` — 用真实 arXiv 论文（PersonalAI）跑出的新知识报告；
- `brain/arxiv_org_html_2506_17001v2.*` — 该次运行的"大脑判定"（分解 / 分类 / 摘要），可重跑：
  ```bash
  python3 -m knowpipe process --url https://arxiv.org/html/2506.17001v2 \
      --brain manual --manual-dir demo/brain --out demo/report_personalai.md
  ```

## 已知边界（MVP）

- 全量摘要通道 = LLM 概括（openai/manual）或原文摘录（heuristic），非逐句精读。
- 语义去重的质量取决于大脑（LLM）与记忆库的丰富度；`review` 反馈是校准它的关键。
- 尚未做：间隔重复调度（下一步：把 new 卡接 SM-2 算法排期）、知识图谱/多跳、视频自动拉流、操作层。

## LLM 环境变量（--brain auto / openai 时读取）
支持多套命名，按优先级 fallback：
- `OPENAI_BASE_URL` / `KNOWPIPE_OPENAI_BASE_URL` / `OPENAI_API_BASE`：API 网关地址
- `OPENAI_API_KEY` / `KNOWPIPE_OPENAI_API_KEY`：API 密钥
- `KNOWPIPE_LLM_MODEL` / `KNOWPIPE_OPENAI_MODEL` / `OPENAI_MODEL`：模型 ID（如 gpt-5.6-terra）

示例（ekti.cc 中转）：
```bash
export OPENAI_BASE_URL="https://chat.ekti.cc/v1"
export OPENAI_API_KEY="sk-xxx"
export KNOWPIPE_LLM_MODEL="gpt-5.6-terra"
```

`--brain auto`：检测到 API key 就用 LLM，没有则降级 heuristic（离线兜底）。
`--brain openai`：强制用 LLM，key 缺失或网络不通会直接报错。

## 环境

- 纯 Python 3.10+，标准库实现，无第三方依赖；`urllib` 抓取网页，`sha1`/字符 n-gram TF-IDF 做候选召回。
