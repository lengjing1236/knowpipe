"""arXiv 数据源采集器：调用公开 API，产出未校验的 Document 字段字典。"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Iterator

logger = logging.getLogger(__name__)

API_URL = "http://export.arxiv.org/api/query"
PAGE_SIZE = 100  # arXiv API 建议单页不超过此值，避免限流
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
MAX_FETCH_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


class CollectError(Exception):
    pass


def _fetch_page(category: str, start: int, page_size: int) -> str:
    params = {
        "search_query": f"cat:{category}",
        "start": start,
        "max_results": page_size,
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "knowpipe-mining/0.1"})

    last_error: Exception | None = None
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning("arXiv 请求失败（category=%s start=%s 第 %d/%d 次尝试）：%s",
                            category, start, attempt, MAX_FETCH_RETRIES, e)
            if attempt < MAX_FETCH_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise CollectError(f"arXiv API 请求失败（已重试 {MAX_FETCH_RETRIES} 次）: {last_error}")


REQUEST_DELAY_SECONDS = 3  # arXiv API 使用规范要求请求间隔不少于 3 秒，否则可能被限流(429)


def collect(target_count: int, category: str = "cs.DC") -> Iterator[dict[str, Any]]:
    """采集至少 target_count 条 arXiv CS 类别标题/摘要元数据，转换为 Document 统一字段结构。"""
    start = 0
    collected = 0
    first_page = True
    while collected < target_count:
        if not first_page:
            time.sleep(REQUEST_DELAY_SECONDS)
        first_page = False
        xml_text = _fetch_page(category, start, PAGE_SIZE)
        root = ET.fromstring(xml_text)
        entries = root.findall("atom:entry", ATOM_NS)
        if not entries:
            break
        for entry in entries:
            if collected >= target_count:
                break
            arxiv_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
            arxiv_id = arxiv_id.rsplit("/", 1)[-1]
            title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
            summary = re.sub(r"\s+", " ", summary)
            updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NS)
            categories = [c.get("term") for c in entry.findall("atom:category", ATOM_NS) if c.get("term")]
            yield {
                "doc_id": arxiv_id,
                "source": "arxiv",
                "source_site": category,
                "title": title,
                "body_text": summary,
                "body_raw": "",
                "tags": categories,
                "language": "en",
                "created_at": updated,
                "source_url": entry.findtext("atom:id", default="", namespaces=ATOM_NS),
                "license": "unknown",
            }
            collected += 1
        start += PAGE_SIZE
