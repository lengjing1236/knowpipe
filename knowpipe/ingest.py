"""ingest.py — 信息摄入与清洗：URL / 文本 / 本地文件 → 纯文本。"""
from __future__ import annotations

import html as html_mod
import os
import re
import urllib.request

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class IngestError(Exception):
    pass


def fetch_url(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise IngestError(f"抓取失败 {url}: {e}")


def html_to_text(html_text: str) -> str:
    """极简 HTML→纯文本：去脚本/样式/标签、反转义、折叠空白。MVP 够用。"""
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def load_source(url=None, text=None, text_file=None, file_path=None,
                max_chars: int = 30000) -> str:
    """返回清洗后的纯文本。优先级：url > file_path > text_file > text。"""
    raw = None
    if url:
        raw = fetch_url(url)
        if re.search(r"(?i)<html|<body|<article", raw[:2000]):
            raw = html_to_text(raw)
    elif file_path:
        if not os.path.exists(file_path):
            raise IngestError(f"文件不存在: {file_path}")
        with open(file_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    elif text_file:
        if not os.path.exists(text_file):
            raise IngestError(f"文件不存在: {text_file}")
        with open(text_file, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    elif text:
        raw = text
    else:
        raise IngestError("必须提供 url / text / text_file / file 之一")

    raw = raw.strip()
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n[……（超长已截断）]"
    return raw


def chunk_text(text: str, max_chars: int = 6000, overlap: int = 200) -> list[str]:
    """按段落边界切块，保证单块不超过 max_chars，块间少量重叠保持语义连续。"""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        if cur and cur_len + len(p) + 1 > max_chars:
            chunks.append("\n".join(cur))
            tail = "\n".join(cur)[-overlap:]
            cur, cur_len = [tail] if tail else [], len(tail)
        cur.append(p)
        cur_len += len(p) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [""]
