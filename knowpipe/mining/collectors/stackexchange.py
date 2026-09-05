"""StackExchange 数据源采集器：调用公开 API，产出未校验的 Document 字段字典。"""
from __future__ import annotations

import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator

import time

logger = logging.getLogger(__name__)

API_URL = "https://api.stackexchange.com/2.3/questions"
PAGE_SIZE = 100  # StackExchange API 单页上限
MAX_PAGE_WITHOUT_KEY = 25  # 不带 app key 时的分页上限（第 26 页起要求 access token）

# 调研文档《课程项目选题调研与实施方案.md》第 2.1 节列出的 4 个技术问答站点，
# 每站不带 key 最多可采 25*100=2500 条，4 站合计 10,000 条，正好对应 FR-008 的
# 规模门槛，不需要申请 Stack Exchange app key。
DEFAULT_SITES = ("stackoverflow", "serverfault", "superuser", "askubuntu")


class CollectError(Exception):
    pass


def _html_to_text(html: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


MAX_FETCH_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


def _fetch_page(site: str, page: int) -> dict[str, Any]:
    params = {
        "site": site, "page": page, "pagesize": PAGE_SIZE,
        "order": "desc", "sort": "activity", "filter": "withbody",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "knowpipe-mining/0.1"})

    last_error: Exception | None = None
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                import json
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning("StackExchange 请求失败（site=%s page=%s 第 %d/%d 次尝试）：%s",
                            site, page, attempt, MAX_FETCH_RETRIES, e)
            if attempt < MAX_FETCH_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise CollectError(f"StackExchange API 请求失败（已重试 {MAX_FETCH_RETRIES} 次）: {last_error}")


def _record_from_item(item: dict[str, Any], site: str) -> dict[str, Any]:
    # StackExchange 的 question_id 只在单个站点内唯一（stackoverflow/askubuntu/superuser/
    # serverfault 各自独立编号），不同站点可能出现相同数字 id。而 mongo_sink 里 documents
    # 集合的唯一键是 (source, doc_id)，source 对所有 StackExchange 站点都是常量
    # "stackexchange"，所以 doc_id 必须自带站点前缀才能保证跨站点全局唯一，否则会在
    # upsert 时把两个不同站点的不同问题互相覆盖（曾在 9,600+400 条规模的真实采集中
    # 观察到 11 条这样的静默覆盖）。
    return {
        "doc_id": f"{site}-{item.get('question_id', '')}",
        "source": "stackexchange",
        "source_site": site,
        "title": item.get("title", ""),
        "body_text": _html_to_text(item.get("body", "")),
        "body_raw": item.get("body", ""),
        "tags": item.get("tags", []),
        "language": "en",
        "created_at": datetime.fromtimestamp(
            item.get("creation_date", 0), tz=timezone.utc
        ).isoformat(),
        "source_url": item.get("link", ""),
        "license": item.get("content_license", "unknown"),
    }


def collect(target_count: int, sites: tuple[str, ...] = DEFAULT_SITES) -> Iterator[dict[str, Any]]:
    """采集至少 target_count 条 StackExchange 问答，按 sites 顺序轮换，转换为 Document
    统一字段结构。单站超过 API 分页上限（page 25，即 2500 条）或数据耗尽后自动换下一站。
    """
    collected = 0
    for site in sites:
        if collected >= target_count:
            break
        page = 1
        while collected < target_count and page <= MAX_PAGE_WITHOUT_KEY:
            data = _fetch_page(site, page)
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                if collected >= target_count:
                    break
                yield _record_from_item(item, site)
                collected += 1
            page += 1
            backoff = data.get("backoff")
            if backoff:
                time.sleep(backoff)
            if not data.get("has_more"):
                break
