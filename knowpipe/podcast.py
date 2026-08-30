"""podcast.py — Podcast RSS 输入适配器。

优先读取节目提供的 transcript（包括 ``podcast:transcript`` 扩展），没有文字稿
时再下载音频并尝试本地 faster-whisper。适配器只负责“找到一集并得到文本”，
后续知识处理继续复用现有 pipeline。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .ingest import html_to_text

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class PodcastError(Exception):
    pass


# 常驻 watcher 可能连续转写多集；Whisper 模型初始化/加载通常比单集推理更慢，
# 按配置复用实例，避免每一集重新读取数百 MB 模型。
_WHISPER_MODELS = {}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _child_text(element, names):
    wanted = {name.lower() for name in names}
    for child in element.iter():
        if child is element or _local_name(child.tag) not in wanted:
            continue
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _first_attr(element, names):
    wanted = {name.lower() for name in names}
    for key, value in element.attrib.items():
        if _local_name(key) in wanted and value:
            return value.strip()
    return ""


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise PodcastError(f"Podcast 请求失败 {url}: {exc}") from exc


def _absolute_url(value: str, base_url: str) -> str:
    return urllib.parse.urljoin(base_url, value.strip()) if value else ""


def _parse_episode(element, feed_url: str) -> dict:
    title = _child_text(element, {"title"}) or "未命名节目"
    description = _child_text(element, {"summary", "description", "content"})
    guid = _child_text(element, {"guid", "id"})
    link = _child_text(element, {"link"})
    if not link:
        for child in element.iter():
            if _local_name(child.tag) == "link":
                link = _first_attr(child, {"href", "url"})
                if link:
                    break
    enclosure_url = ""
    enclosure_type = ""
    for child in element.iter():
        if _local_name(child.tag) == "enclosure":
            enclosure_url = _first_attr(child, {"url", "href"})
            enclosure_type = _first_attr(child, {"type"})
            break
    # Atom feeds commonly represent the audio as <link rel="enclosure" href="..."/>.
    if not enclosure_url:
        for child in element.iter():
            if _local_name(child.tag) != "link":
                continue
            rel = _first_attr(child, {"rel"}).lower()
            if rel != "enclosure":
                continue
            enclosure_url = _first_attr(child, {"href", "url"})
            enclosure_type = _first_attr(child, {"type"})
            break

    transcripts = []
    for child in element.iter():
        if _local_name(child.tag) != "transcript":
            continue
        url = _first_attr(child, {"url", "href", "src"}) or (child.text or "").strip()
        if url:
            transcripts.append({
                "url": _absolute_url(url, feed_url),
                "type": _first_attr(child, {"type", "mime", "mimetype"}),
                "language": _first_attr(child, {"language", "lang"}),
            })

    published = _child_text(element, {"pubdate", "published", "updated", "date"})
    return {
        "title": title,
        "description": description,
        "guid": guid or link or title,
        "link": _absolute_url(link, feed_url),
        "audio_url": _absolute_url(enclosure_url, feed_url),
        "audio_type": enclosure_type,
        "transcripts": transcripts,
        "published": published,
    }


def fetch_feed(feed_url: str, timeout: int = 30) -> dict:
    """读取 RSS/Atom feed，返回节目标题和标准化 episode 列表。"""
    body = _http_get(feed_url, timeout=timeout)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PodcastError(f"Podcast RSS/XML 解析失败: {exc}") from exc

    channel_title = _child_text(root, {"title"}) or feed_url
    entries = [
        element for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    if not entries:
        raise PodcastError("Podcast feed 中没有找到 episode/item")
    return {
        "url": feed_url,
        "title": channel_title,
        "episodes": [_parse_episode(element, feed_url) for element in entries],
    }


def _clean_transcript(text: str) -> str:
    """清洗 VTT/SRT/HTML/JSON transcript，只保留可送入 LLM 的文本。"""
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if data is not None:
        pieces = []

        def visit(value):
            if isinstance(value, dict):
                for key in ("text", "content", "transcript"):
                    if isinstance(value.get(key), str) and value[key].strip():
                        pieces.append(value[key].strip())
                for key, child in value.items():
                    if key not in {"text", "content", "transcript"}:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        if pieces:
            raw = "\n".join(pieces)

    if re.search(r"(?i)<html|<body|<p\b", raw):
        raw = html_to_text(raw)
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.upper() in {"WEBVTT", "NOTE"}:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        if re.search(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->", s):
            continue
        s = re.sub(r"^\[[0-9:. -]+\]\s*", "", s)
        lines.append(s)
    return "\n".join(lines).strip()


def fetch_transcript(url: str, timeout: int = 30) -> str:
    """获取并清洗一个 transcript URL。"""
    body = _http_get(url, timeout=timeout)
    return _clean_transcript(body.decode("utf-8", errors="replace"))


def _safe_name(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return stem[:80] or "episode"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def cleanup_transcript_cache(cache_dir: str, retention_days: int = 30) -> int:
    """删除超过保留期的 transcript 缓存，返回删除数量。

    ``retention_days <= 0`` 表示清理目录内所有 ``.txt``；清理失败的文件会被
    跳过，不影响 watcher 处理新集。
    """
    if not cache_dir or not os.path.isdir(cache_dir):
        return 0
    cutoff = time.time() - max(0, int(retention_days)) * 86400
    removed = 0
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".txt"):
            continue
        path = os.path.join(cache_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) <= cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed


def download_audio(url: str, output_dir: str, cache_key: str, timeout: int = 60) -> str:
    """下载 episode 音频并缓存，返回本地路径。"""
    os.makedirs(output_dir, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in {".mp3", ".m4a", ".mp4", ".wav", ".flac", ".ogg", ".webm"}:
        ext = ".audio"
    filename = f"{_safe_name(cache_key)}{ext}"
    path = os.path.join(output_dir, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response, open(path, "wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(path)
        except OSError:
            pass
        raise PodcastError(f"Podcast 音频下载失败: {exc}") from exc
    return path


def transcribe_local(audio_path: str) -> str:
    """使用 faster-whisper 转写 Podcast 音频。"""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise PodcastError(
            "未安装 faster-whisper，无法转写 Podcast 音频；"
            "可安装 `pip install faster-whisper`，或让节目提供 transcript。"
        ) from exc
    model_size = os.getenv("WHISPER_MODEL", "small")
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("WHISPER_COMPUTE", "int8")
    language = os.getenv("PODCAST_WHISPER_LANGUAGE") or None
    model_key = (model_size, device, compute_type)
    model = _WHISPER_MODELS.get(model_key)
    if model is None:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _WHISPER_MODELS[model_key] = model
    segments, _info = model.transcribe(audio_path, language=language, vad_filter=True)
    return "\n".join(segment.text.strip() for segment in segments if segment.text.strip())


def fetch_episode_item(feed_url: str, item: dict, transcriber: str = "auto",
                       transcript_url: str | None = None,
                       audio_dir: str = "cache/podcast_audio",
                       transcript_cache_dir: str = "cache/podcast_transcripts",
                       keep_audio: bool | None = None) -> dict:
    """获取一个已经从 feed 解析出的 episode 的文本。

    ``watch`` 使用此接口按 GUID 处理节目，而不是依赖 RSS 当前排序；这样
    插入置顶/重排历史节目不会误处理另一集。``item`` 应来自
    :func:`fetch_feed` 的 ``episodes`` 列表。
    """
    if not isinstance(item, dict):
        raise PodcastError("episode metadata 必须是对象")
    item = dict(item)
    item.setdefault("title", "未命名节目")
    item.setdefault("guid", item.get("link") or item["title"])
    item.setdefault("transcripts", [])
    if not isinstance(item.get("transcripts"), list):
        item["transcripts"] = []
    item.setdefault("audio_url", "")
    cache_key = hashlib.sha1(
        f"{feed_url}\n{item['guid']}".encode("utf-8")
    ).hexdigest()[:20]
    cache_path = os.path.join(transcript_cache_dir, f"{cache_key}.txt")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8", errors="replace") as cached:
            text = cached.read().strip()
        if text:
            return {
                "title": item["title"], "text": text, "method": "cache",
                "feed_url": feed_url, "episode": item.get("episode"), "episode_meta": item,
            }
    candidates = []
    if transcript_url:
        candidates.append(_absolute_url(transcript_url, feed_url))
    candidates.extend(t["url"] for t in item["transcripts"] if t.get("url"))
    if transcriber in {"auto", "transcript"}:
        for url in candidates:
            try:
                text = fetch_transcript(url)
            except PodcastError:
                continue
            if text:
                os.makedirs(transcript_cache_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as cached:
                    cached.write(text)
                return {
                    "title": item["title"], "text": text, "method": "transcript",
                    "feed_url": feed_url, "episode": item.get("episode"), "episode_meta": item,
                }
        if transcriber == "transcript":
            raise PodcastError("该 episode 没有可读取的 transcript")

    if transcriber in {"auto", "whisper"}:
        if not item["audio_url"]:
            raise PodcastError("该 episode 没有音频 enclosure，无法使用 Whisper")
        audio_path = download_audio(item["audio_url"], audio_dir, cache_key)
        try:
            text = transcribe_local(audio_path)
        finally:
            # 音频通常比 transcript 大两个数量级；默认转写完成即删除。
            retain = (_env_flag("KNOWPIPE_PODCAST_KEEP_AUDIO", False)
                      if keep_audio is None else bool(keep_audio))
            if not retain:
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass
        if text.strip():
            os.makedirs(transcript_cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as cached:
                cached.write(text)
            return {
                "title": item["title"], "text": text, "method": "whisper",
                "feed_url": feed_url, "episode": item.get("episode"), "episode_meta": item,
            }
    raise PodcastError(f"episode {item.get('guid') or item.get('title')} 未得到可用文本")


def fetch_episode_by_guid(feed_url: str, guid: str, transcriber: str = "auto",
                          transcript_url: str | None = None,
                          audio_dir: str = "cache/podcast_audio",
                          transcript_cache_dir: str = "cache/podcast_transcripts",
                          keep_audio: bool | None = None) -> dict:
    """按稳定的 episode GUID 获取一集文本。

    RSS 的排序可能随时间变化，因此自动轮询应使用 GUID，而非 ``episode=1``
    之类的位置编号。找不到 GUID 时抛出 :class:`PodcastError`。
    """
    feed = fetch_feed(feed_url)
    wanted = str(guid).strip()
    for item in feed["episodes"]:
        if str(item.get("guid", "")).strip() == wanted:
            return fetch_episode_item(
                feed_url, item, transcriber=transcriber,
                transcript_url=transcript_url, audio_dir=audio_dir,
                transcript_cache_dir=transcript_cache_dir, keep_audio=keep_audio,
            )
    raise PodcastError(f"feed 中找不到 episode GUID: {guid}")


def fetch_episode(feed_url: str, episode: int = 1, transcriber: str = "auto",
                  transcript_url: str | None = None,
                  audio_dir: str = "cache/podcast_audio",
                  transcript_cache_dir: str = "cache/podcast_transcripts",
                  keep_audio: bool | None = None) -> dict:
    """按 feed 中的序号选择 episode（兼容手动 CLI 用法）。"""
    if episode < 1:
        raise PodcastError("episode 必须从 1 开始")
    feed = fetch_feed(feed_url)
    if episode > len(feed["episodes"]):
        raise PodcastError(f"Podcast 共 {len(feed['episodes'])} 集，请求 episode={episode}")
    result = fetch_episode_item(
        feed_url, feed["episodes"][episode - 1], transcriber=transcriber,
        transcript_url=transcript_url, audio_dir=audio_dir,
        transcript_cache_dir=transcript_cache_dir, keep_audio=keep_audio,
    )
    # 保持旧 API 返回的序号字段，同时不把内部序号混进 episode_meta。
    result["episode"] = episode
    return result
