"""处理批次统计：生成 batch_id、记录运行统计（input/valid/skipped/failed 计数）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def new_batch_id(now: datetime | None = None) -> str:
    """生成本次运行的 batch_id，格式 {YYYYMMDD}-{8位随机十六进制}（data-model.md Entity: Batch）。"""
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%d}-{uuid.uuid4().hex[:8]}"


class BatchStats:
    """累积单次运行的统计计数，运行结束后通过 to_dict() 落盘。"""

    def __init__(self, batch_id: str, sources: list[str]):
        self.batch_id = batch_id
        self.sources = sources
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self.input_count = 0
        self.valid_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self.status = "running"
        self.error_message: str | None = None

    def finish(self, status: str = "success", error_message: str | None = None) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.status = status
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input_count": self.input_count,
            "valid_count": self.valid_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "sources": self.sources,
            "status": self.status,
            "error_message": self.error_message,
        }


def build_batch_stats(sources: list[str], input_count: int, valid_count: int,
                       skipped_count: int, failed_count: int,
                       batch_id: str | None = None) -> dict[str, Any]:
    """一次性组装 Batch 实体记录（不经过 BatchStats 累积过程时使用）。"""
    stats = BatchStats(batch_id or new_batch_id(), sources)
    stats.input_count = input_count
    stats.valid_count = valid_count
    stats.skipped_count = skipped_count
    stats.failed_count = failed_count
    stats.finish()
    return stats.to_dict()
