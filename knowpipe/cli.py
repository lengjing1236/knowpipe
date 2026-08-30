"""cli.py — Knowpipe 命令行入口。

用法示例：
  # 给记忆库播种（代表"你已知的内容"）
  python -m knowpipe seed --file demo/seed_cards.jsonl

  # 处理一篇文章，输出新知识报告
  python -m knowpipe process --url https://... --out report.md --brain auto
  python -m knowpipe process --file article.txt --brain heuristic
  python -m knowpipe process --bilibili BV1DfrdByE2H --p 1 --out r.md --brain auto
  python -m knowpipe process --podcast https://example.com/feed.xml --episode 1

  # 合集批量：循环每个分P，共享记忆库去重，输出汇总报告
  python -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages 1-3 --out batch.md
  python -m knowpipe process --bilibili BV1DfrdByE2H --bili-pages all --out all.md

  # 查看记忆库 / 给新卡评分（反馈回路）
  python -m knowpipe status
  python -m knowpipe ask "我的记忆库里如何解释事件循环？"
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
from .report import (build_report, build_batch_report, build_article_report,
                     build_integrated_report)

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

    embedder = (brain.embed_texts
                if getattr(brain, "embedding_model", "") else None)
    candidates_map = {
        i: store.candidates_for(c["claim"], embedder=embedder)
        for i, c in enumerate(cards)
    }
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
        # 报告不应在摘要失败时回退为 ASR 原文片段；宁可明确提示未生成概括。
        digest = "（当前大脑未生成概括）"

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
                          certainty=c.get("certainty", "fact"), status=store_mod.STATUS_NEW,
                          persist=False)
            res["new_cards"].append(item)
        elif v["verdict"] == "known":
            if against_id:
                store.touch_known(against_id, persist=False)
            res["skipped"].append(item)
        elif v["verdict"] == "refine":
            store.add_new(claim=claim, source=res["source"], topic=c.get("topic", ""),
                          certainty=c.get("certainty", "fact"),
                          status=store_mod.STATUS_REFINED, relates_to=against_id,
                          persist=False)
            res["refined"].append(item)
        elif v["verdict"] == "contradict":
            store.add_new(claim=claim, source=res["source"], topic=c.get("topic", ""),
                          certainty=c.get("certainty", "fact"),
                          status=store_mod.STATUS_CONFLICT, relates_to=against_id,
                          persist=False)
            res["conflicted"].append(item)
        else:
            raise BrainError(f"未知 verdict: {v['verdict']}")
    # 原子卡判定全部完成后一次落盘，避免逐卡重写整个 JSONL。
    store.save()
    res["store_stats"] = store.stats()
    return res


def run_integrated_pipeline(text, source, title, input_id, brain, store):
    """同时生成长文总结和知识差分卡；OpenAI 模式并行执行两条支路。"""
    article_executor = None
    article_future = None
    if brain.provider == "openai":
        article_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        article_future = article_executor.submit(
            brain.generate_article_from_transcript, text
        )
    try:
        res = run_pipeline(text, source, title, input_id, brain, store)
        article = (article_future.result() if article_future is not None
                   else brain.generate_article_from_transcript(text))
    finally:
        if article_executor is not None:
            article_executor.shutdown(wait=True)
    res["input_chars"] = len(text)
    res["article"] = article
    return res

def cmd_seed(args):
    store = store_mod.open_store(args.memory)
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
                persist=False,
            )
            n += 1
    if n:
        store.save()
    print(f"已播种 {n} 张卡 → {store.path}（总量 {len(store.cards)}）")

def cmd_process(args):
    store = store_mod.open_store(args.memory)
    try:
        brain = Brain(provider=args.brain, manual_dir=args.manual_dir)
    except BrainError as e:
        sys.exit(f"[brain] {e}")

    source_args = [name for name in ("bilibili", "podcast", "url", "text", "text_file", "file")
                   if getattr(args, name, None)]
    if len(source_args) > 1:
        sys.exit("输入来源参数只能指定一个：" + ", ".join(source_args))

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
    elif args.podcast:
        from . import podcast as podcast_mod
        try:
            podcast_src = podcast_mod.fetch_episode(
                args.podcast, episode=args.episode,
                transcriber=args.podcast_transcriber,
                transcript_url=args.podcast_transcript,
                audio_dir=args.podcast_audio_dir,
                transcript_cache_dir=args.podcast_cache_dir,
            )
        except podcast_mod.PodcastError as exc:
            sys.exit(f"[podcast] {exc}")
        text = podcast_src["text"]
        title = args.title or podcast_src["title"]
        source = f"podcast:{args.podcast} E{args.episode}"
        input_id = f"podcast_{_input_id(args.podcast)}_e{args.episode}"
        print(f"[ingest] Podcast 第{args.episode}集 {len(text)} 字"
              f"（方式:{podcast_src['method']}）")
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

    if getattr(args, "mode", "cards") == "integrated":
        res = run_integrated_pipeline(text, source, title, input_id, brain, store)
        article = res["article"]
        report = build_integrated_report(res, article)
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


def _batch_checkpoint_path(cache_dir, bvid):
    """返回合集任务检查点路径；检查点和逐字稿一样属于本地运行时数据。"""
    return os.path.join(cache_dir, f"{bvid}.batch.json")


def _save_batch_checkpoint(path, bvid, requested_pages, brain_provider, page_results,
                           memory_path=None, mode="cards", model=None):
    """原子写入合集检查点，避免中途终止留下半个 JSON。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "bvid": bvid,
        "requested_pages": requested_pages,
        "brain_provider": brain_provider,
        "memory_path": os.path.abspath(memory_path) if memory_path else None,
        "mode": mode,
        "model": model,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "page_results": page_results,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_batch_checkpoint(path, bvid, requested_pages, brain_provider,
                           memory_path=None, mode="cards", model=None):
    """读取与本次任务参数匹配的检查点；损坏或过期时返回空结果。"""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError, TypeError):
        return []
    if (payload.get("bvid") != bvid
            or payload.get("brain_provider") != brain_provider
            or payload.get("requested_pages") != requested_pages
            or payload.get("mode", "cards") != mode
            or (memory_path and payload.get("memory_path")
                and payload.get("memory_path") != os.path.abspath(memory_path))
            or (model and payload.get("model") and payload.get("model") != model)):
        return []
    rows = payload.get("page_results", [])
    if not isinstance(rows, list):
        return []
    # 失败页不算完成，下次 --bili-resume 会自动重试。
    return [r for r in rows if isinstance(r, dict) and not r.get("error")]

def cmd_bili_batch(args, store, brain):
    """合集批量：循环每个分P 各自进管道，共享记忆库去重，输出汇总报告。"""
    from . import bilibili as bili_mod

    info = bili_mod.resolve_bvid(args.bilibili)
    pages = _parse_bili_pages(args.bili_pages, info["pages"])
    if not pages:
        sys.exit(f"--bili-pages '{args.bili_pages}' 解析为空（合集共 {len(info['pages'])}P）")
    cache_dir = args.bili_cache_dir or os.path.join("cache", "bili_transcripts")
    checkpoint_path = _batch_checkpoint_path(cache_dir, args.bilibili)
    page_results = []
    if getattr(args, "bili_resume", False):
        page_results = _load_batch_checkpoint(
            checkpoint_path, args.bilibili, pages, brain.provider,
            memory_path=args.memory, mode=getattr(args, "mode", "cards"),
            model=getattr(brain, "model", None))
        if page_results:
            print(f"[batch] 从检查点恢复 {len(page_results)}P：{checkpoint_path}")

    print(f"[batch] {info['title']}")
    print(f"[batch] 合集共 {len(info['pages'])}P，本次处理 {len(pages)}P：{pages}")
    print(f"[batch] 逐字稿缓存：{cache_dir}（命中则跳过ASR）")

    completed_pages = {r.get("page") for r in page_results}
    for idx, page in enumerate(pages, 1):
        if page in completed_pages:
            print(f"[batch] [{idx}/{len(pages)}] P{page} 已在检查点完成，跳过管道处理")
            continue
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
            if getattr(args, "bili_resume", False):
                _save_batch_checkpoint(
                    checkpoint_path, args.bilibili, pages, brain.provider,
                    [r for r in page_results if not r.get("error")],
                    memory_path=args.memory, mode=getattr(args, "mode", "cards"),
                    model=getattr(brain, "model", None))
            continue

        text = bili_src["text"]
        source = f"bilibili:{args.bilibili} P{page} {part}".strip()
        title = f"{info['title']} P{page} {part}"
        input_id = f"bili_{args.bilibili}_p{page}"
        print(f"[ingest] B站 {args.bilibili} P{page} 逐字稿 {len(text)} 字"
              f"（方式:{bili_src['method']}）")
        if getattr(args, "mode", "cards") == "integrated":
            res = run_integrated_pipeline(text, source, title, input_id, brain, store)
        else:
            res = run_pipeline(text, source, title, input_id, brain, store)
        res["page"] = page
        res["part"] = part
        page_results.append(res)
        if getattr(args, "bili_resume", False):
            _save_batch_checkpoint(
                checkpoint_path, args.bilibili, pages, brain.provider,
                [r for r in page_results if not r.get("error")],
                memory_path=args.memory, mode=getattr(args, "mode", "cards"),
                model=getattr(brain, "model", None))

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
    store = store_mod.open_store(args.memory)
    st = store.stats()
    print(f"记忆库: {st['path']}")
    print(f"总量: {st['total']}  分布: {st['by_status']}")
    for c in store.cards[:20]:
        print(f"  [{c['status']:9s}] {c['id']} {c['claim'][:60]}")


def cmd_ask(args):
    """检索个人记忆库并回答一个问题。"""
    question = args.question.strip()
    if not question:
        sys.exit("问题不能为空")
    store = store_mod.open_store(args.memory)
    try:
        brain = Brain(provider=args.brain)
    except BrainError as e:
        sys.exit(f"[brain] {e}")
    embedder = (brain.embed_texts
                if getattr(brain, "embedding_model", "") else None)
    candidates = store.candidates_for(question, top_k=args.top_k, embedder=embedder)
    answer = brain.answer_question(question, candidates)
    lines = [f"# 记忆库问答", "", f"**问题**：{question}", "", answer, ""]
    useful = [(card, score) for card, score in candidates if score > 0]
    if useful:
        lines.extend(["## 检索依据", ""])
        for card, score in useful[:args.top_k]:
            lines.append(f"- [{card['id']}]（相似度 {score:.3f}）{card['claim']}")
    report = "\n".join(lines) + "\n"
    if args.out:
        parent = os.path.dirname(args.out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[ask] 已写入 {args.out}")
    else:
        print(report)
    return {"question": question, "answer": answer, "candidates": candidates}

def cmd_review(args):
    store = store_mod.open_store(args.memory)
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
            store.delete(c["id"])
        elif ans == "q":
            break


def cmd_migrate(args):
    """把旧 JSONL 记忆库迁移到 SQLite。"""
    try:
        n = store_mod.migrate_jsonl_to_sqlite(args.source, args.destination)
    except (OSError, ValueError, json.JSONDecodeError, store_mod.sqlite3.Error) as e:
        sys.exit(f"迁移失败：{e}")
    print(f"已迁移 {n} 张卡 → {args.destination}")


def _read_feed_urls(args):
    """合并重复的 --feed 与 --feeds-file，忽略空行和 # 注释。"""
    urls = list(getattr(args, "feed", None) or [])
    feeds_file = getattr(args, "feeds_file", None)
    if feeds_file:
        try:
            with open(feeds_file, encoding="utf-8") as handle:
                urls.extend(
                    line.split("#", 1)[0].strip()
                    for line in handle
                    if line.split("#", 1)[0].strip()
                )
        except OSError as exc:
            sys.exit(f"订阅源文件读取失败：{exc}")
    result = []
    for url in urls:
        if url and url not in result:
            result.append(url)
    if not result:
        sys.exit("至少指定一个 --feed URL，或通过 --feeds-file 提供订阅源列表")
    return result


def cmd_watch(args):
    """轮询 Podcast RSS，处理新集并归档报告；``--once`` 用于单次运行。"""
    from .watch import watch_forever, watch_once

    feeds = _read_feed_urls(args)
    if args.interval <= 0:
        sys.exit("--interval 必须大于 0 秒")
    if args.limit is not None and args.limit < 1:
        sys.exit("--limit 必须大于 0")
    smtp = {
        "host": args.smtp_host,
        "port": args.smtp_port,
        "username": args.smtp_user,
        "password": args.smtp_password,
        "from": args.smtp_from,
        "to": args.smtp_to,
        "tls": args.smtp_tls,
    }
    common = dict(
        memory_path=args.memory, state_path=args.state, archive_dir=args.archive_dir,
        mode=args.mode, brain_provider=args.brain, manual_dir=args.manual_dir,
        transcriber=args.podcast_transcriber, transcript_url=args.podcast_transcript,
        audio_dir=args.podcast_audio_dir, transcript_cache_dir=args.podcast_cache_dir,
        transcript_retention_days=args.transcript_retention_days,
        keep_audio=args.keep_audio, webhook_url=args.webhook_url, smtp=smtp,
        limit=args.limit,
    )
    try:
        if args.once:
            results = watch_once(feeds, **common)
            succeeded = sum(1 for r in results if not r.get("error"))
            failed = sum(1 for r in results if r.get("error"))
            print(f"[watch] 本轮完成：成功 {succeeded}，失败 {failed}，状态库 {args.state}")
            return results
        print(f"[watch] 常驻轮询：每 {args.interval} 秒检查 {len(feeds)} 个订阅源（Ctrl-C 退出）")
        watch_forever(feeds, interval=args.interval, **common)
    except BrainError as exc:
        sys.exit(f"[brain] {exc}")
    except KeyboardInterrupt:
        print("\n[watch] 已停止")

def build_parser():
    p = argparse.ArgumentParser(prog="knowpipe", description="知识精炼管道 MVP")
    p.add_argument("--memory", default=MEMORY_DEFAULT,
                   help="记忆库路径；.db/.sqlite 使用 SQLite，其它路径使用 JSONL")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="导入种子知识卡（代表你已知的内容）")
    s.add_argument("--file", required=True, help="JSONL 种子文件")
    s.set_defaults(fn=cmd_seed)

    pr = sub.add_parser("process", help="摄入并处理内容，输出新知识报告")
    pr.add_argument("--bilibili", help="B站 BV 号（自动取逐字稿，默认第1P）")
    pr.add_argument("--podcast", help="Podcast RSS/Atom feed URL（默认处理第1集）")
    pr.add_argument("--episode", type=int, default=1, help="Podcast 集数（从1开始，默认1）")
    pr.add_argument("--podcast-transcript", default=None,
                    help="直接指定本集 transcript URL（优先于 RSS 中的 transcript）")
    pr.add_argument("--podcast-transcriber", default="auto",
                    choices=["auto", "transcript", "whisper"],
                    help="Podcast 文本来源：auto=transcript→Whisper；transcript=仅文字稿；whisper=强制本地 Whisper")
    pr.add_argument("--podcast-audio-dir", default="cache/podcast_audio",
                    help="Podcast 音频缓存目录（默认 cache/podcast_audio）")
    pr.add_argument("--podcast-cache-dir", default="cache/podcast_transcripts",
                    help="Podcast transcript 缓存目录（默认 cache/podcast_transcripts）")
    pr.add_argument("--p", type=int, default=1, help="B站分P页码（默认1）")
    pr.add_argument("--bili-pages", default=None,
                    help="合集批量：分P范围，如 '1-3,5' 或 'all'（与 --bilibili 配合，共享记忆库去重）")
    pr.add_argument("--bili-resume", action="store_true",
                    help="合集批量启用断点续跑（检查点保存在 --bili-cache-dir）")
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
    pr.add_argument("--mode", default="cards", choices=["cards", "article", "integrated"],
                    help="输出模式：cards=原子知识卡；article=长文总结；integrated=长文+知识差分卡")
    pr.add_argument("--manual-dir", default=None, help="manual provider 判定文件目录")
    pr.set_defaults(fn=cmd_process)

    st = sub.add_parser("status", help="查看记忆库状态")
    st.set_defaults(fn=cmd_status)

    aq = sub.add_parser("ask", help="基于个人记忆库回答问题")
    aq.add_argument("question", help="要回答的问题")
    aq.add_argument("--top-k", type=int, default=8, help="召回知识卡数量（默认8）")
    aq.add_argument("--brain", default="auto", choices=["auto", "openai", "heuristic"],
                    help="回答模型（默认 auto）")
    aq.add_argument("--out", default=None, help="回答输出路径 (md，默认 stdout)")
    aq.set_defaults(fn=cmd_ask)

    rv = sub.add_parser("review", help="给新知识卡评分（反馈回路）")
    rv.add_argument("--apply", default=None, help="评分 JSONL 文件（非交互）")
    rv.set_defaults(fn=cmd_review)

    mg = sub.add_parser("migrate", help="将 JSONL 记忆库迁移为 SQLite")
    mg.add_argument("--source", required=True, help="源 JSONL 文件")
    mg.add_argument("--destination", required=True, help="目标 SQLite 文件（建议 .db）")
    mg.set_defaults(fn=cmd_migrate)

    wt = sub.add_parser(
        "watch", aliases=["podcast-watch"],
        help="自动轮询 Podcast RSS，处理新集并归档报告",
    )
    # 全局 --memory 通常写在子命令前；这里额外允许写在 watch 后，且不覆盖全局值。
    wt.add_argument("--memory", default=argparse.SUPPRESS,
                    help="记忆库路径（可覆盖全局 --memory）")
    wt.add_argument("--feed", action="append", default=None,
                    help="Podcast RSS/Atom URL；可重复指定多个")
    wt.add_argument("--feeds-file", default=None,
                    help="订阅源列表文件（每行一个 URL，# 开头为注释）")
    wt.add_argument("--state", default="state/podcast_watch.db",
                    help="episode 状态 SQLite 路径（默认 state/podcast_watch.db）")
    wt.add_argument("--archive-dir", default="archive/podcasts",
                    help="报告归档目录（默认 archive/podcasts）")
    wt.add_argument("--once", action="store_true",
                    help="只轮询一次（适合 cron；默认常驻轮询）")
    wt.add_argument("--interval", type=int, default=3600,
                    help="常驻轮询间隔秒数，默认 3600（建议 1800~3600）")
    wt.add_argument("--limit", type=int, default=None,
                    help="每个 feed 每轮最多处理几集；不指定则处理全部未处理集")
    wt.add_argument("--mode", default="integrated", choices=["cards", "article", "integrated"],
                    help="处理模式，默认 integrated（长文总结+未知知识卡）")
    wt.add_argument("--brain", default="auto",
                    choices=["auto", "openai", "manual", "heuristic"],
                    help="大脑 provider，默认 auto")
    wt.add_argument("--manual-dir", default=None, help="manual provider 判定文件目录")
    wt.add_argument("--podcast-transcript", default=None,
                    help="覆盖所有 episode 的 transcript URL（可选）")
    wt.add_argument("--podcast-transcriber", default="auto",
                    choices=["auto", "transcript", "whisper"],
                    help="文本来源：transcript→Whisper 自动降级")
    wt.add_argument("--podcast-audio-dir", default="cache/podcast_audio",
                    help="Podcast 音频缓存目录")
    wt.add_argument("--podcast-cache-dir", default="cache/podcast_transcripts",
                    help="Podcast transcript 缓存目录")
    wt.add_argument("--transcript-retention-days", type=int, default=30,
                    help="transcript 本地缓存保留天数，默认30；设为0清理全部过期缓存")
    wt.add_argument("--keep-audio", action=argparse.BooleanOptionalAction, default=None,
                    help="是否保留 Whisper 下载的音频（默认转写后立即删除）")
    wt.add_argument("--webhook-url", default=None,
                    help="可选手机通知 webhook（也可用 KNOWPIPE_WEBHOOK_URL）")
    wt.add_argument("--smtp-host", default=None, help="可选 SMTP 主机")
    wt.add_argument("--smtp-port", type=int, default=None, help="SMTP 端口，默认 587")
    wt.add_argument("--smtp-user", default=None, help="SMTP 用户名")
    wt.add_argument("--smtp-password", default=None, help="SMTP 密码（更建议用环境变量）")
    wt.add_argument("--smtp-from", default=None, help="发件人地址")
    wt.add_argument("--smtp-to", default=None, help="收件人地址，可用逗号分隔多个")
    wt.add_argument("--smtp-tls", action=argparse.BooleanOptionalAction, default=None,
                    help="是否启用 STARTTLS（默认启用）")
    wt.set_defaults(fn=cmd_watch)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
