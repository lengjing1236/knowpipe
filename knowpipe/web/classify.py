"""known/refine/new/possible_conflict 判定规则（纯 Python，不经 Spark，research.md §2）。"""
from __future__ import annotations

from typing import Any

STATUS_KNOWN = "known"
STATUS_REFINE = "refine"
STATUS_NEW = "new"
STATUS_POSSIBLE_CONFLICT = "possible_conflict"

VALID_STATUSES = (STATUS_KNOWN, STATUS_REFINE, STATUS_NEW, STATUS_POSSIBLE_CONFLICT)


def classify_unit(unit: dict[str, Any], known_keywords: set[str],
                   known_topics: set[str]) -> dict[str, Any]:
    """对单个知识单元做集合匹配判定，返回 known/refine/new 三态之一
    （possible_conflict 由 apply_conflict_override 单独叠加，见 data-model.md State Transitions）。

    unit 需包含 knowledge_id/keywords/topic_cluster_id/source/doc_id 字段
    （见 mongo_sink.get_knowledge_units）。
    """
    unit_keywords = set(unit.get("keywords") or [])
    matched_keywords = sorted(unit_keywords & known_keywords)
    topic_id = unit.get("topic_cluster_id")
    matched_topic = topic_id is not None and str(topic_id) in known_topics

    if matched_keywords and matched_topic:
        status = STATUS_KNOWN
    elif matched_topic:
        # 命中已知主题簇但没有关键词层面的直接命中：视为该主题下的新增内容。
        status = STATUS_REFINE
    elif matched_keywords:
        status = STATUS_KNOWN
    else:
        status = STATUS_NEW

    return {
        "knowledge_id": unit["knowledge_id"],
        "source": unit.get("source"),
        "doc_id": unit.get("doc_id"),
        "status": status,
        "matched_keywords": matched_keywords,
        "matched_topic_cluster_id": topic_id if matched_topic else None,
        "conflict_evidence": None,
        "pending_review": False,
    }


def apply_conflict_override(classified: dict[str, Any], conflict_evidence: dict[str, Any] | None,
                             pending_review: bool = True) -> dict[str, Any]:
    """叠加 LLM 生成的候选冲突证据（research.md §3）：
    - 无证据（conflict_evidence 为空/无 snippet）：不改变原判定（对应 FR-003 降级）。
    - 有证据但 pending_review=True（人工复核前）：对外仍展示原判定（new/refine），
      不切换为 possible_conflict。
    - 有证据且 pending_review=False（人工复核通过）：对外展示为 possible_conflict。
    """
    result = dict(classified)
    has_evidence = bool(conflict_evidence and conflict_evidence.get("snippet"))
    if not has_evidence:
        return result
    result["conflict_evidence"] = conflict_evidence
    result["pending_review"] = pending_review
    if not pending_review:
        result["status"] = STATUS_POSSIBLE_CONFLICT
    return result


def classify_all(units: list[dict[str, Any]], known_keywords: list[str],
                  known_topics: list[str]) -> list[dict[str, Any]]:
    """对一批知识单元批量判定（供画像变更后的重新判定调用，research.md §5）。"""
    kw_set = set(known_keywords)
    topic_set = {str(t) for t in known_topics}
    return [classify_unit(unit, kw_set, topic_set) for unit in units]
