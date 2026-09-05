"""两级去重：精确去重（source+source_url）与内容哈希近似去重（跨来源）。"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def exact_key(record: dict[str, Any]) -> tuple[str, str]:
    """返回精确去重键：(source, source_url)，对应 research.md §2 第一级判定。"""
    return record["source"], record["source_url"]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_hash(record: dict[str, Any]) -> str:
    """对标题+正文规范化后计算 SHA-256 内容哈希，对应 research.md §2 第二级判定。"""
    normalized = _normalize(record.get("title", "")) + "\n" + _normalize(record.get("body_text", ""))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_exact_duplicate(record: dict[str, Any], seen_exact_keys: set[tuple[str, str]]) -> bool:
    """判断本条记录是否已存在于本批次已处理的精确去重键集合中。"""
    return exact_key(record) in seen_exact_keys


def is_content_duplicate(record: dict[str, Any], seen_hashes: set[str]) -> bool:
    """判断本条记录的内容哈希是否已存在于本批次已处理的哈希集合中（跨来源近似去重）。"""
    return content_hash(record) in seen_hashes
