"""Podcast RSS 自动轮询与处理调度。

该模块把 RSS、Podcast 文本适配器和既有 Knowpipe pipeline 串起来。它不通过
子进程调用 CLI，因此 cron、测试和常驻模式都能复用同一套 Python API。
"""
from __future__ import annotations

import hashlib
import os
import re
import time

from . import podcast
from . import store as store_mod
from .brain import Brain
from .notify import build_payload, send_notifications
from .report import build_article_report, build_integrated_report, build_report
from .watch_store import WatchStore


DEFAULT_STATE = os.path.join("state", "podcast_watch.db")
DEFAULT_ARCHIVE = os.path.join("archive", "podcasts")


def _safe_name(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", str(value or ""))
    return stem.strip("._")[:100] or "episode"


def _episode_date(item: dict) -> str:
    value = str(item.get("published") or "")
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", value)
    return "-".join(m.groups()) if m else time.strftime("%Y-%m-%d")


def _report_path(archive_dir: str, feed_title: str, item: dict) -> str:
    guid = str(item.get("guid") or item.get("title") or "episode")
    digest = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:10]
    directory = os.path.join(archive_dir, _safe_name(feed_title))
    filename = f"{_episode_date(item)}_{_safe_name(item.get('title'))}_{digest}.md"
    return os.path.abspath(os.path.join(directory, filename))


def _write_atomic(path: str, content: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp, path)


def _input_id(feed_url: str, guid: str) -> str:
    return "podcast_" + hashlib.sha1(
        f"{feed_url}\n{guid}".encode("utf-8")
    ).hexdigest()[:20]


def _notification_digest(result: dict) -> str:
    """把“新知识卡”放在通知摘要最前面，再附上全文概括。"""
    claims = [c.get("claim", "") for c in result.get("new_cards", []) if c.get("claim")]
    claims += [c.get("claim", "") for c in result.get("refined", []) if c.get("claim")]
    digest = str(result.get("digest") or result.get("article") or "").strip()
    if claims:
        prefix = "新知识：" + "；".join(claims[:6])
        return prefix + ("\n\n概括：" + digest if digest else "")
    return digest


def process_episode(feed_url: str, feed: dict, item: dict, *, brain, store,
                    archive_dir: str, mode: str = "integrated",
                    transcriber: str = "auto", transcript_url: str | None = None,
                    audio_dir: str = "cache/podcast_audio",
                    transcript_cache_dir: str = "cache/podcast_transcripts",
                    keep_audio: bool | None = None) -> dict:
    """处理单集并原子归档报告，返回 pipeline 结果及 ``report_path``。"""
    source = f"podcast:{feed_url}#{item.get('guid', '')}"
    title = str(item.get("title") or "未命名节目")
    fetch_kwargs = {
        "transcriber": transcriber,
        "transcript_url": transcript_url,
        "audio_dir": audio_dir,
        "transcript_cache_dir": transcript_cache_dir,
    }
    if keep_audio is not None:
        fetch_kwargs["keep_audio"] = keep_audio
    fetched = podcast.fetch_episode_item(feed_url, item, **fetch_kwargs)
    text = str(fetched.get("text") or "").strip()
    if not text:
        raise podcast.PodcastError("episode transcript 为空")
    print(f"[watch] {title}：取得 {len(text)} 字文本（方式:{fetched.get('method', '?')}）")
    input_id = _input_id(feed_url, str(item.get("guid") or title))

    if mode == "article":
        article = brain.generate_article_from_transcript(text)
        result = {
            "title": title, "source": source, "brain_provider": brain.provider,
            "article": article, "digest": article, "input_chars": len(text),
        }
        report = build_article_report(title, source, len(text), article, brain.provider)
    elif mode == "integrated":
        from .cli import run_integrated_pipeline
        result = run_integrated_pipeline(text, source, title, input_id, brain, store)
        report = build_integrated_report(result, result.get("article", ""))
    elif mode == "cards":
        from .cli import run_pipeline
        result = run_pipeline(text, source, title, input_id, brain, store)
        report = build_report(result)
    else:
        raise ValueError(f"未知处理模式: {mode}")

    path = _report_path(archive_dir, str(feed.get("title") or feed_url), item)
    _write_atomic(path, report)
    result["report_path"] = path
    result["episode_meta"] = item
    return result


def watch_once(feed_urls, *, memory_path: str = "memory/cards.jsonl",
               state_path: str = DEFAULT_STATE, archive_dir: str = DEFAULT_ARCHIVE,
               mode: str = "integrated", brain_provider: str = "auto",
               manual_dir: str | None = None, transcriber: str = "auto",
               transcript_url: str | None = None,
               audio_dir: str = "cache/podcast_audio",
               transcript_cache_dir: str = "cache/podcast_transcripts",
               transcript_retention_days: int | None = 30,
               keep_audio: bool | None = None,
               webhook_url: str | None = None, smtp: dict | None = None,
               limit: int | None = None, stale_after: int = 6 * 3600,
               brain=None, memory_store=None, state_store=None) -> list[dict]:
    """轮询所有 feed 一次，处理未成功集并返回结果列表。

    ``brain``、``memory_store`` 和 ``state_store`` 可注入测试替身；正常 CLI
    调用只需提供路径。单次轮询是幂等的，重复执行不会再次调用 LLM。
    """
    urls = []
    for value in feed_urls or []:
        value = str(value).strip()
        if value and value not in urls:
            urls.append(value)
    if not urls:
        raise ValueError("至少需要一个 Podcast feed URL")
    if brain is None:
        brain = Brain(provider=brain_provider, manual_dir=manual_dir)
    own_store = memory_store is None
    own_state = state_store is None
    store = memory_store or store_mod.open_store(memory_path)
    state = state_store or WatchStore(state_path)
    outcomes = []
    try:
        if transcript_retention_days is not None:
            try:
                removed = podcast.cleanup_transcript_cache(
                    transcript_cache_dir, transcript_retention_days
                )
                if removed:
                    print(f"[watch] 清理过期 transcript：{removed} 个")
            except (TypeError, ValueError):
                # 保留策略是 housekeeping，配置错误不应阻断本轮处理。
                pass
        for feed_url in urls:
            try:
                feed = podcast.fetch_feed(feed_url)
                state.upsert_feed(feed_url, feed.get("title", feed_url))
                state.mark_feed_checked(feed_url)
            except Exception as exc:  # noqa: BLE001 - one broken feed must not stop others
                message = str(exc)
                try:
                    state.upsert_feed(feed_url, feed_url)
                    state.mark_feed_checked(feed_url, error=message)
                except Exception:
                    pass
                outcomes.append({"feed_url": feed_url, "error": message})
                print(f"[watch] feed 轮询失败 {feed_url}: {message}")
                continue

            count = 0
            for item in feed.get("episodes", []):
                if limit is not None and limit > 0 and count >= limit:
                    break
                row = state.discover_episode(feed_url, item)
                guid = row["guid"]
                if row.get("status") == "success":
                    continue
                if not state.claim_episode(feed_url, guid, stale_after=stale_after):
                    continue
                count += 1
                try:
                    result = process_episode(
                        feed_url, feed, item, brain=brain, store=store,
                        archive_dir=archive_dir, mode=mode, transcriber=transcriber,
                        transcript_url=transcript_url, audio_dir=audio_dir,
                        transcript_cache_dir=transcript_cache_dir, keep_audio=keep_audio,
                    )
                    state.mark_success(feed_url, guid, result["report_path"])
                    digest = _notification_digest(result)
                    payload = build_payload(
                        feed_title=feed.get("title", feed_url), title=item.get("title", ""),
                        digest=digest, report_path=result["report_path"], source=result.get("source", ""),
                    )
                    try:
                        notification_errors = send_notifications(
                            payload, webhook_url=webhook_url, smtp=smtp
                        )
                    except Exception as exc:  # noqa: BLE001 - notify must not undo success
                        notification_errors = [f"通知失败: {exc}"]
                    result["notification_errors"] = notification_errors
                    if notification_errors:
                        print(f"[watch] 通知失败（报告已保留）：{'; '.join(notification_errors)}")
                    result["feed_url"] = feed_url
                    result["guid"] = guid
                    outcomes.append(result)
                    print(f"[watch] 已归档：{result['report_path']}")
                except Exception as exc:  # noqa: BLE001 - retain failed state for retry
                    message = str(exc)
                    state.mark_failed(feed_url, guid, message)
                    failed = {"feed_url": feed_url, "guid": guid, "title": item.get("title", ""),
                              "error": message}
                    outcomes.append(failed)
                    print(f"[watch] 处理失败 {item.get('title', guid)}: {message}")
    finally:
        if own_state:
            state.close()
        if own_store and hasattr(store, "close"):
            store.close()
    return outcomes


def watch_forever(feed_urls, *, interval: int = 3600, **kwargs):
    """常驻轮询；默认每小时检查一次，Ctrl-C 可安全退出。"""
    if interval <= 0:
        raise ValueError("interval 必须大于 0 秒")
    while True:
        watch_once(feed_urls, **kwargs)
        time.sleep(interval)
