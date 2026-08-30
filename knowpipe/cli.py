"""cli.py — Knowpipe 命令行入口。

用法示例：
  # 给记忆库播种（代表"你已知的内容"）
  python -m knowpipe seed --file demo/seed_cards.jsonl

  # 处理一篇文章，输出新知识报告
  python -m knowpipe process --url https://... --out report.md --brain auto
  python -m knowpipe process --file article.txt --brain heuristic
  python -m knowpipe process --bilibili BV1DfrdByE2H --p 1 --out r.md --brain auto

  # 合集批量：循环每个分P，共享记忆库去重，输出汇总报告
  python -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages 1-3 --out batch.md
  python -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages all --out all.md

  # 查看记忆库 / 给新卡评分（反馈回路）
  python -m knowpipe status
  python -m knowpipe review
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time

from . import ingest, store as store_mod
from .brain import Brain, BrainError
from .report import build_report, build_batch_report, build_article_report

MEMORY_DEFAULT = os.path.join("memory", "cards.jsonl")

def _input_id(url=None, file_path=None, text_file=None):
    if url:
        name = re.sub(r"[^A-Za-z0-9]+", "_", url.split("//")[-1])[:80]
    elif file_path:
        name = os.path.splitext(os.path.basename(file_path))[0]
    elif text_file:
        name = os.path.splitext(os.path.basename(text_file))[0]
    else:
        name = "stdin"
    return re.sub(r"_+", "_", name).strip("_") or "input"

def run_pipeline(text, source, title, input_id, brain, store):
    """对一段文本跑完整管道：切块→抽原子卡→语义差分→回写记忆库。返回 res dict。

    合集批量时每页调用一次，共享同一个 store → 跨P自动去重。
    """
    chunks = ingest.chunk_text(text)
    print(f"[ingest] 清洗后 {len(text)} 字 → {len(chunks)} 块")

    # 全文摘要与分块抽取/判定相互独立，提前发起可隐藏一次网络往返。
    summary_executor = None
    summary_future = None
    if brain.provider == "openai":
        summary_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        summary_future = summary_executor.submit(brain.summarize, text, input_id)

    # 分块之间互不依赖；OpenAI 模式并行请求可显著降低长视频等待时间。
    # 通过环境变量可调节并发，设为 1 即恢复串行（适用于严格限流的网关）。
    decompose_results = []
    if brain.provider == "openai" and len(chunks) > 1:
        try:
            configured = int(os.getenv("KNOWPIPE_DECOMPOSE_WORKERS", "4"))
            workers = max(1, min(len(chunks), configured))
        except ValueError:
            workers = min(len(chunks), 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(brain.decompose_chunk, chunk, input_id, ci)
                       for ci, chunk in enumerate(chunks)]
            decompose_results = [f.result() for f in futures]
    else:
        decompose_results = [brain.decompose_chunk(chunk, input_id, ci)
                             for ci, chunk in enumerate(chunks)]
    cards = []
    for ci, got in enumerate(decompose_results):
        cards.extend(got)
        print(f"[decompose] 块{ci}: {len(got)} 张卡")
    print(f"[decompose] 共 {len(cards)} 张原子卡")

    candidates_map = {i: store.candidates_for(c["claim"]) for i, c in enumerate(cards)}
    verdicts = brain.classify_cards(cards, candidates_map, input_id)
    by_verdict = {}
    for v in verdicts:
        by_verdict.setdefault(v["verdict"], 0)
        by_verdict[v["verdict"]] += 1
    print(f"[classify] 判定分布: {by_verdict}")

    if summary_future is not None:
        try:
            digest = summary_future.result()
        finally:
            summary_executor.shutdown(wait=True)
    else:
        digest = brain.summarize(text, input_id)
    if not digest:
        digest = ("（离线模式未生成概括）" + text[:300].replace("\n", " ").strip()
                  + ("…" if len(text) > 300 else ""))

    res = {
        "title": title, "source": source,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "brain_provider": brain.provider,
        "n_chunks": len(chunks), "n_cards": len(cards),
        "new_cards": [], "refined": [], "conflicted": [], "skipped": [],
        "digest": digest,
    }
    for i, c in enumerate(cards):
        v = verdicts[i]
        claim = c["claim"]
        against_id = v.get("against_id")
        against = store.get(against_id) if against_id else None
        item = dict(c)
        item.update({
            "verdict": v["verdict"], "confidence": v.get("confidence"),
            "against_id": against_id,
            "against_claim": against["claim"] if against else None,
            "reason": v.get("reason", ""),
        })
        if v["verdict"] == "new":
            store.add_new(claim=claim, source=res["source"], topic=c.get("topic", ""),
                          certainty=c.get("certainty", "fact"), status=store_mod.STATUS_NEW)
            res["new_cards"].append(item)
        elif v["verdict"] == "known":
            if against_id:
                store.touch_known(against_id)
            res["skipped"].append(item)
        elif v["verdict"] == "refine":
            store.add_new(claim=claim, source=res["source"], topic=c.get("topic", ""),
                          certainty=c.get("certainty", "fact"),
                          status=store_mod.STATUS_REFINED, relates_to=against_id)
            res["refined"].append(item)
        elif v["verdict"] == "contradict":
            store.add_new(claim=claim, source=res["source"], topic=c.get("topic", ""),
                          certainty=c.get("certainty", "fact"),
                          status=store_mod.STATUS_CONFLICT, relates_to=against_id)
            res["conflicted"].append(item)
        else:
            raise BrainError(f"未知 verdict: {v['verdict']}")
    res["store_stats"] = store.stats()
    return res

def cmd_seed(args):
    store = store_mod.MemoryStore(args.memory)
    if not os.path.exists(args.file):
        sys.exit(f"种子文件不存在: {args.file}")
    n = 0
    with open(args.file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            claim = row.get("claim")
            if not claim:
                continue
            store.add_new(
                claim=claim,
                source=row.get("source", "seed"),
                topic=row.get("topic", ""),
                certainty=row.get("certainty", "fact"),
                status=row.get("status", store_mod.STATUS_KNOWN),
            )
            n += 1
    print(f"已播种 {n} 张卡 → {store.path}（总量 {len(store.cards)}）")

def cmd_process(args):
    store = store_mod.MemoryStore(args.memory)
    try:
        brain = Brain(provider=args.brain, manual_dir=args.manual_dir)
    except BrainError as e:
        sys.exit(f"[brain] {e}")

    # 合集批量模式
    if args.bilibili and args.bili_pages:
        return cmd_bili_batch(args, store, brain)

    # ---- 单条摄入 ----
    if args.bilibili:
        from . import bilibili as bili_mod
        cache_dir = args.bili_cache_dir or os.path.join("cache", "bili_transcripts")
        bili_src = bili_mod.fetch_transcript_cached(
            args.bilibili, page=args.p, cache_dir=cache_dir,
            transcriber=args.bili_transcriber, workdir=args.bili_workdir,
            whisper_model=args.whisper_model)
        text = bili_src["text"]
        title = args.title or bili_src["title"]
        source = f"bilibili:{args.bilibili} P{args.p} {bili_src['part']}".strip()
        input_id = f"bili_{args.bilibili}_p{args.p}"
        print(f"[ingest] B站 {args.bilibili} 第{args.p}P 逐字稿 {len(text)} 字"
              f"（方式:{bili_src['method']}）")
    else:
        text = ingest.load_source(url=args.url, text=args.text, text_file=args.text_file,
                                  file_path=args.file)
        if not text.strip():
            sys.exit("摄入内容为空")
        title = args.title or (args.url or args.file or args.text_file or "文本输入")
        source = args.url or args.file or args.text_file or "text"
        input_id = _input_id(args.url, args.file, args.text_file)

    # 文章级模式：一次完成 ASR 清洗与知识总结，不抽原子卡
    if getattr(args, "mode", "cards") == "article":
        print("[article] 一次完成 ASR 纠错与知识总结（分段+名词解释+逻辑修复）…")
        article = brain.generate_article_from_transcript(text)
        report = build_article_report(title, source, text, article, brain.provider)
        out_path = args.out
        if out_path:
            d = os.path.dirname(out_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"[report] 已写入 {out_path}")
        else:
            print(report)
        return {"title": title, "source": source, "article": article}

    res = run_pipeline(text, source, title, input_id, brain, store)
    report = build_report(res)
    out_path = args.out
    if out_path:
        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[report] 已写入 {out_path}")
    else:
        print(report)
    return res

def _parse_bili_pages(spec, all_pages):
    """解析 '1-3,5' 或 'all' → 排序去重的页码列表。"""
    max_page = max(p["page"] for p in all_pages)
    if spec == "all":
        return list(range(1, max_page + 1))
    result = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            result.extend(range(int(lo), int(hi) + 1))
        else:
            result.append(int(token))
    return sorted(set(p for p in result if 1 <= p <= max_page))

def cmd_bili_batch(args, store, brain):
    """合集批量：循环每个分P 各自进管道，共享记忆库去重，输出汇总报告。"""
    from . import bilibili as bili_mod

    info = bili_mod.resolve_bvid(args.bilibili)
    pages = _parse_bili_pages(args.bili_pages, info["pages"])
    if not pages:
        sys.exit(f"--bili-pages '{args.bili_pages}' 解析为空（合集共 {len(info['pages'])}P）")
    cache_dir = args.bili_cache_dir or os.path.join("cache", "bili_transcripts")

    print(f"[batch] {info['title']}")
    print(f"[batch] 合集共 {len(info['pages'])}P，本次处理 {len(pages)}P：{pages}")
    print(f"[batch] 逐字稿缓存：{cache_dir}（命中则跳过ASR）")

    page_results = []
    for idx, page in enumerate(pages, 1):
        part = info["pages"][page - 1]["part"] if page <= len(info["pages"]) else f"P{page}"
        print(f"\n{'='*60}")
        print(f"[batch] [{idx}/{len(pages)}] P{page}：{part}")
        print(f"{'='*60}")
        try:
            bili_src = bili_mod.fetch_transcript_cached(
                args.bilibili, page=page, cache_dir=cache_dir,
                transcriber=args.bili_transcriber, workdir=args.bili_workdir,
                whisper_model=args.whisper_model)
        except bili_mod.BiliError as e:
            print(f"[batch] P{page} 取稿失败，跳过：{e}")
            page_results.append({"page": page, "part": part, "error": str(e),
                                 "n_cards": 0, "new_cards": [], "refined": [],
                                 "conflicted": [], "skipped": [], "digest": "",
                                 "brain_provider": brain.provider})
            continue

        text = bili_src["text"]
        source = f"bilibili:{args.bilibili} P{page} {part}".strip()
        title = f"{info['title']} P{page} {part}"
        input_id = f"bili_{args.bilibili}_p{page}"
        print(f"[ingest] B站 {args.bilibili} P{page} 逐字稿 {len(text)} 字"
              f"（方式:{bili_src['method']}）")
        res = run_pipeline(text, source, title, input_id, brain, store)
        res["page"] = page
        res["part"] = part
        page_results.append(res)

    # 汇总报告
    report = build_batch_report(info, page_results, store.stats())
    out_path = args.out
    if out_path:
        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[batch] 汇总报告已写入 {out_path}")
    else:
        print(report)
    return page_results

def cmd_status(args):
    store = store_mod.MemoryStore(args.memory)
    st = store.stats()
    print(f"记忆库: {st['path']}")
    print(f"总量: {st['total']}  分布: {st['by_status']}")
    for c in store.cards[:20]:
        print(f"  [{c['status']:9s}] {c['id']} {c['claim'][:60]}")

def cmd_review(args):
    store = store_mod.MemoryStore(args.memory)
    pending = [c for c in store.cards
               if c.get("status") in (store_mod.STATUS_NEW, store_mod.STATUS_REFINED,
                                      store_mod.STATUS_CONFLICT)]
    if not pending:
        print("没有待评分的卡（状态为 new/refined/conflict 的卡）。")
        return

    if args.apply:
        rows = []
        with open(args.apply, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        ok = 0
        for row in rows:
            c = store.get(row.get("id", ""))
            if not c or row.get("verdict") not in (store_mod.STATUS_KNOWN, store_mod.STATUS_LEARNED):
                continue
            store.update_status(c["id"], row["verdict"], note=row.get("note"))
            ok += 1
        print(f"[review] 已应用 {ok}/{len(rows)} 条评分")
        return

    if not sys.stdin.isatty():
        print("非交互终端：请用 --apply <file> 传入评分文件，或直接运行 review 交互模式。")
        return

    print("逐条评分：k=早已知  l=学到新东西  s=跳过  x=删除  q=退出")
    for c in pending:
        print("\n---")
        print(f"[{c['id']}] {c['claim']}")
        if c.get("relates_to"):
            rel = store.get(c["relates_to"])
            if rel:
                print(f"  关联: {rel['claim'][:60]}")
        ans = input("k/l/s/x/q > ").strip().lower()
        if ans == "k":
            store.update_status(c["id"], store_mod.STATUS_KNOWN)
        elif ans == "l":
            store.update_status(c["id"], store_mod.STATUS_LEARNED)
        elif ans == "x":
            store.cards = [x for x in store.cards if x["id"] != c["id"]]
            store.save()
        elif ans == "q":
            break

def build_parser():
    p = argparse.ArgumentParser(prog="knowpipe", description="知识精炼管道 MVP")
    p.add_argument("--memory", default=MEMORY_DEFAULT, help="记忆库路径 (JSONL)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="导入种子知识卡（代表你已知的内容）")
    s.add_argument("--file", required=True, help="JSONL 种子文件")
    s.set_defaults(fn=cmd_seed)

    pr = sub.add_parser("process", help="摄入并处理内容，输出新知识报告")
    pr.add_argument("--bilibili", help="B站 BV 号（自动取逐字稿，默认第1P）")
    pr.add_argument("--p", type=int, default=1, help="B站分P页码（默认1）")
    pr.add_argument("--bili-pages", default=None,
                    help="合集批量：分P范围，如 '1-3,5' 或 'all'（与 --bilibili 配合，共享记忆库去重）")
    pr.add_argument("--bili-cache-dir", default=None,
                    help="逐字稿缓存目录（默认 cache/bili_transcripts），命中则跳过ASR")
    pr.add_argument("--bili-transcriber", default="auto",
                    choices=["auto", "subtitle", "lark", "whisper"],
                    help="B站逐字稿来源：auto=字幕→飞书妙记→本地whisper自动降级；subtitle=仅官方字幕；lark=强制妙记；whisper=强制本地whisper")
    pr.add_argument("--whisper-model", default=None,
                    help="本地whisper模型大小（tiny/base/small/medium/large-v3），默认small；仅whisper后端生效，首次会下载模型")
    pr.add_argument("--bili-workdir", default=".",
                    help="转写中间产物目录（默认当前目录下 lark_out/ 或 whisper_out/）")
    pr.add_argument("--url", help="文章 URL")
    pr.add_argument("--text", help="直接传入文本")
    pr.add_argument("--text-file", help="本地文本文件路径")
    pr.add_argument("--file", help="本地文件（txt/md 等）")
    pr.add_argument("--title", help="报告标题")
    pr.add_argument("--out", default=None, help="报告输出路径 (md)")
    pr.add_argument("--brain", default="auto",
                    choices=["auto", "openai", "manual", "heuristic"],
                    help="大脑 provider")
    pr.add_argument("--mode", default="cards", choices=["cards", "article"],
                    help="输出模式：cards=原子知识卡+novelty判定（默认）；article=ASR清洗+知识总结文章（推荐技术视频）")
    pr.add_argument("--manual-dir", default=None, help="manual provider 判定文件目录")
    pr.set_defaults(fn=cmd_process)

    st = sub.add_parser("status", help="查看记忆库状态")
    st.set_defaults(fn=cmd_status)

    rv = sub.add_parser("review", help="给新知识卡评分（反馈回路）")
    rv.add_argument("--apply", default=None, help="评分 JSONL 文件（非交互）")
    rv.set_defaults(fn=cmd_review)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
