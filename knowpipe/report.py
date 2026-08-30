"""report.py — 生成新知识报告（Markdown）。"""
from __future__ import annotations

import time


def _qa(claim, idx):
    return f"{idx}. **问**：{claim}\n    **答**：见下方知识卡（主动回忆后对照）。"


def build_report(res):
    L = []
    L.append(f"# 新知识报告 — {res['title']}")
    L.append("")
    L.append(f"- **来源**：{res['source']}")
    L.append(f"- **处理时间**：{res['time']}")
    L.append(f"- **大脑**：{res['brain_provider']}")
    L.append(f"- **输入块数**：{res['n_chunks']}，**抽取原子卡**：{res['n_cards']}")
    L.append("")
    L.append("## 结果一览")
    L.append("")
    L.append("| 类别 | 数量 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| 🆕 新知识 | {len(res['new_cards'])} | 记忆库没有，值得学习 |")
    L.append(f"| 🔧 深化/更正 | {len(res['refined'])} | 对已知概念的补充或修正 |")
    L.append(f"| ⚖️ 冲突待裁决 | {len(res['conflicted'])} | 与记忆库矛盾，需要你判断 |")
    L.append(f"| ⏭️ 已跳过(已知) | {len(res['skipped'])} | 与记忆库重复，不重复推 |")
    L.append("")
    L.append("## 1. 新知识（本次重点）")
    L.append("")
    if res["new_cards"]:
        by_topic = {}
        for c in res["new_cards"]:
            by_topic.setdefault(c.get("topic") or "未分类", []).append(c)
        for topic, cards in sorted(by_topic.items()):
            L.append(f"### {topic}")
            for c in cards:
                L.append(f"- **{c['claim']}**")
                if c.get("evidence"):
                    L.append(f"  - 原文依据：> {c['evidence']}")
        L.append("")
    else:
        L.append("（无）")
        L.append("")
    if res["refined"]:
        L.append("## 2. 深化 / 更正（不是全新，但对旧认知有增量）")
        L.append("")
        for c in res["refined"]:
            against = c.get("against_claim") or c.get("relates_to") or "?"
            L.append(f"- **{c['claim']}**  (针对已有卡：{against})")
        L.append("")
    if res["conflicted"]:
        L.append("## 3. 冲突（需要你裁决）")
        L.append("")
        for c in res["conflicted"]:
            L.append(f"- **{c['claim']}**")
            L.append(f"  - 与已有卡冲突：{c.get('against_claim', '?')}")
            L.append(f"  - 裁决建议：若新信息正确，把旧卡标记为 superseded（后续版本支持）")
        L.append("")
    L.append("## 4. 已跳过的已知内容（抽查过滤质量用）")
    L.append("")
    if res["skipped"]:
        for c in res["skipped"][:15]:
            L.append(f"- ~~{c['claim']}~~  → 命中 {c.get('against_claim', '?')}")
        if len(res["skipped"]) > 15:
            L.append(f"- … 共 {len(res['skipped'])} 条被过滤")
    else:
        L.append("（无）")
    L.append("")
    L.append("## 5. 建议复习的问答卡（主动回忆，优于重读）")
    L.append("")
    if res["new_cards"]:
        for idx, c in enumerate(res["new_cards"], 1):
            L.append(f"{idx}. **Q**: {c['claim']}？  — 先在脑中作答，再回看知识卡对照。")
        L.append("")
    else:
        L.append("（无新知识，无需新增复习卡）")
        L.append("")
    L.append("## 6. 全量摘要（防回音壁，快速扫一遍找遗漏）")
    L.append("")
    L.append(f"> {res['digest']}")
    L.append("")
    st = res["store_stats"]
    L.append("## 7. 记忆库状态")
    L.append("")
    L.append(f"- 总量：{st['total']} 张卡；分布：{st['by_status']}")
    L.append("- 提示：用 `review` 命令给新知识卡打分，过滤会越来越准。")
    L.append("")
    return "\n".join(L)


def build_batch_report(info, page_results, store_stats):
    """合集批量汇总报告。info=resolve_bvid结果；page_results=每页run_pipeline返回。"""
    L = []
    bvid = info["bvid"]
    total_pages = len(info["pages"])
    processed = [r for r in page_results if not r.get("error")]
    failed = [r for r in page_results if r.get("error")]
    brain_provider = processed[0]["brain_provider"] if processed else "?"

    L.append(f"# 合集新知识报告 — {info['title']}")
    L.append("")
    fail_note = f"，失败 {len(failed)}P" if failed else ""
    L.append(f"- **来源**：bilibili:{bvid}（共 {total_pages}P，本次处理 {len(processed)}P{fail_note}）")
    L.append(f"- **处理时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- **大脑**：{brain_provider}")
    L.append("")

    total_cards = sum(r.get("n_cards", 0) for r in processed)
    total_new = sum(len(r.get("new_cards", [])) for r in processed)
    total_refined = sum(len(r.get("refined", [])) for r in processed)
    total_conflict = sum(len(r.get("conflicted", [])) for r in processed)
    total_skipped = sum(len(r.get("skipped", [])) for r in processed)

    L.append("## 汇总一览")
    L.append("")
    L.append("| 指标 | 数量 |")
    L.append("|---|---|")
    L.append(f"| 处理分P | {len(processed)} / {total_pages} |")
    L.append(f"| 抽取原子卡 | {total_cards} |")
    L.append(f"| 🆕 新知识 | {total_new} |")
    L.append(f"| 🔧 深化/更正 | {total_refined} |")
    L.append(f"| ⚖️ 冲突待裁决 | {total_conflict} |")
    L.append(f"| ⏭️ 已跳过(已知，含跨P去重) | {total_skipped} |")
    L.append("")

    L.append("## 逐P统计")
    L.append("")
    L.append("| P | 标题 | 抽卡 | 新 | 深化 | 冲突 | 跳过 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in page_results:
        if r.get("error"):
            L.append(f"| {r['page']} | {r.get('part', '')} | — | — | — | — | 取稿失败 |")
        else:
            L.append(f"| {r['page']} | {r.get('part', '')} | {r['n_cards']} | "
                     f"{len(r['new_cards'])} | {len(r['refined'])} | "
                     f"{len(r['conflicted'])} | {len(r['skipped'])} |")
    L.append("")

    all_new = []
    for r in processed:
        for c in r.get("new_cards", []):
            c2 = dict(c)
            c2["_page"] = r.get("page")
            all_new.append(c2)

    L.append("## 1. 全部新知识（跨P聚合，本次重点）")
    L.append("")
    if all_new:
        by_topic = {}
        for c in all_new:
            by_topic.setdefault(c.get("topic") or "未分类", []).append(c)
        for topic, cards in sorted(by_topic.items()):
            L.append(f"### {topic}")
            for c in cards:
                page_tag = f"[P{c['_page']}]" if c.get("_page") else ""
                L.append(f"- {page_tag} **{c['claim']}**")
            L.append("")
    else:
        L.append("（无）")
        L.append("")

    all_refined = []
    for r in processed:
        for c in r.get("refined", []):
            c2 = dict(c)
            c2["_page"] = r.get("page")
            all_refined.append(c2)
    if all_refined:
        L.append("## 2. 深化 / 更正")
        L.append("")
        for c in all_refined:
            against = c.get("against_claim") or "?"
            L.append(f"- [P{c.get('_page', '?')}] **{c['claim']}**  (针对：{against})")
        L.append("")

    all_conflict = []
    for r in processed:
        for c in r.get("conflicted", []):
            c2 = dict(c)
            c2["_page"] = r.get("page")
            all_conflict.append(c2)
    if all_conflict:
        L.append("## 3. 冲突（需要裁决）")
        L.append("")
        for c in all_conflict:
            L.append(f"- [P{c.get('_page', '?')}] **{c['claim']}**")
            L.append(f"  - 冲突：{c.get('against_claim', '?')}")
        L.append("")

    L.append("## 4. 建议复习的问答卡（主动回忆）")
    L.append("")
    if all_new:
        for idx, c in enumerate(all_new, 1):
            L.append(f"{idx}. **Q**: {c['claim']}？  — 先在脑中作答，再回看知识卡对照。")
        L.append("")
    else:
        L.append("（无新知识）")
        L.append("")

    L.append("## 5. 逐P详情")
    L.append("")
    for r in page_results:
        if r.get("error"):
            L.append(f"### P{r['page']} {r.get('part', '')} — 取稿失败")
            L.append(f"错误：{r['error']}")
            L.append("")
            continue
        L.append(f"### P{r['page']} {r.get('part', '')}")
        L.append(f"- 抽卡 {r['n_cards']}：新 {len(r['new_cards'])} / "
                 f"深化 {len(r['refined'])} / 冲突 {len(r['conflicted'])} / "
                 f"跳过 {len(r['skipped'])}")
        if r.get("digest"):
            d = r["digest"].replace("\n", " ")[:200]
            L.append(f"- 摘要：> {d}")
        if r.get("article"):
            L.append("")
            L.append("#### 长文总结")
            L.append("")
            L.append(r["article"])
        L.append("")

    L.append("## 6. 记忆库状态")
    L.append("")
    L.append(f"- 总量：{store_stats['total']} 张卡；分布：{store_stats['by_status']}")
    L.append("- 跨P去重：共享同一记忆库，后处理的P会自动过滤前面P已写入的新知识。")
    L.append("- 提示：用 `review` 命令给新知识卡打分，过滤会越来越准。")
    L.append("")
    return "\n".join(L)

def build_article_report(title, source, raw_text, *args):
    """文章级报告：只输出知识总结，不泄露 ASR 逐字稿或中间清洗稿。

    兼容旧调用签名 ``(title, source, raw, clean, article, provider)``；
    ``clean`` 参数会被忽略，不再写入报告。
    """
    if len(args) == 2:
        article, brain_provider = args
    elif len(args) == 3:  # 旧签名
        _clean_text, article, brain_provider = args
    else:
        raise TypeError("build_article_report 参数数量不正确")
    L = []
    L.append(f"# 知识整理 — {title}")
    L.append("")
    L.append(f"- **来源**：{source}")
    L.append(f"- **处理时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- **大脑**：{brain_provider}（文章级模式：ASR 清洗 + 知识总结）")
    input_length = raw_text if isinstance(raw_text, int) else len(raw_text)
    L.append(f"- **输入长度**：{input_length} 字")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 知识总结")
    L.append("")
    L.append(article)
    L.append("")
    return "\n".join(L)


def build_integrated_report(res, article):
    """合并长文总结与知识差分报告，不包含 ASR 原稿。"""
    article_part = build_article_report(
        res["title"], res["source"], res.get("input_chars", 0),
        article, res["brain_provider"]
    )
    L = [article_part, "", "---", "", "## 未知知识卡与记忆差分", ""]
    cards_part = build_report(res)
    # 去掉卡片报告自己的一级标题和重复元数据，保留从“结果一览”开始的内容。
    marker = "## 结果一览"
    tail = cards_part[cards_part.find(marker):] if marker in cards_part else cards_part
    L.append(tail)
    return "\n".join(L)
