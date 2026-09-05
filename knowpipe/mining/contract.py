"""文档字段契约：校验原始记录是否满足 data-model.md 中 Document 的必填字段与质量标记。"""
from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = (
    "doc_id", "source", "source_site", "title", "body_text",
    "language", "created_at", "source_url",
)
VALID_SOURCES = ("stackexchange", "arxiv")

# spec.md Assumptions 未固定该阈值，留给实施阶段决定；50 个词是一个保守的起点，
# 后续若发现聚类/关键词质量与该值不符，可在此处单点调整。
SHORT_BODY_WORD_THRESHOLD = 50


class DocumentValidationError(Exception):
    """必填字段缺失或来源枚举不合法时抛出，对应 spec Edge Case 第一条。"""


def validate_document(raw: dict[str, Any]) -> dict[str, Any]:
    """校验单条原始记录，返回补齐 quality 标记后的记录；缺失必填字段则抛出异常。"""
    missing = [f for f in REQUIRED_FIELDS if not raw.get(f)]
    if missing:
        raise DocumentValidationError(f"缺失必填字段: {missing}")
    if raw["source"] not in VALID_SOURCES:
        raise DocumentValidationError(f"source 不合法: {raw['source']!r}")

    record = dict(raw)
    record.setdefault("body_raw", "")
    record.setdefault("tags", [])
    record.setdefault("license", "unknown")
    record.setdefault("mining", None)

    quality = dict(record.get("quality") or {})
    quality.update(compute_quality(record))
    record["quality"] = quality
    return record


def compute_quality(record: dict[str, Any]) -> dict[str, bool]:
    """计算 quality.short_body 等标记（不覆盖去重阶段写入的 dedup_hash/invalid）。"""
    body = record.get("body_text") or ""
    word_count = len(body.split())
    return {"short_body": word_count < SHORT_BODY_WORD_THRESHOLD}
