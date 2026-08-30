"""本地 Podcast 自动化任务状态（SQLite）。

状态库与知识记忆库分开保存：记忆库记录“学到了什么”，这里记录“哪一集已经
处理过”。使用 SQLite 事务和唯一键保证 cron 重入或两个 watcher 同时运行时，
同一个 ``feed_url + episode_guid`` 最多只有一个进程进入 processing 状态。
"""
from __future__ import annotations

import os
import sqlite3
import time


STATUS_DISCOVERED = "discovered"
STATUS_PROCESSING = "processing"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class WatchStore:
    """SQLite-backed feed/episode state store.

    ``state_path`` is runtime data and should normally live under ``state/``. The
    schema is intentionally small and inspectable with any SQLite client.
    """

    def __init__(self, state_path: str = "state/podcast_watch.db"):
        self.path = state_path
        parent = os.path.dirname(os.path.abspath(state_path))
        os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(state_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                last_checked TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS episodes (
                feed_url TEXT NOT NULL,
                guid TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                published TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                audio_url TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'discovered',
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processed_at TEXT,
                report_path TEXT,
                error TEXT,
                PRIMARY KEY (feed_url, guid),
                FOREIGN KEY (feed_url) REFERENCES feeds(url)
            );
            CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
            CREATE INDEX IF NOT EXISTS idx_episodes_updated ON episodes(updated_at);
            """
        )
        self._conn.commit()

    @staticmethod
    def _row(row):
        return dict(row) if row is not None else None

    def upsert_feed(self, url: str, title: str = ""):
        with self._conn:
            self._conn.execute(
                """INSERT INTO feeds(url, title) VALUES (?, ?)
                   ON CONFLICT(url) DO UPDATE SET title=excluded.title""",
                (url, title or url),
            )

    def mark_feed_checked(self, url: str, error: str | None = None):
        with self._conn:
            self._conn.execute(
                "UPDATE feeds SET last_checked=?, last_error=? WHERE url=?",
                (now_iso(), error, url),
            )

    def get_feed(self, url: str):
        return self._row(self._conn.execute("SELECT * FROM feeds WHERE url=?", (url,)).fetchone())

    def discover_episode(self, feed_url: str, item: dict):
        """登记 feed 中看到的 episode，返回当前状态行。

        已成功处理的 episode 只更新 RSS 元数据，不会被重置为 discovered；这
        允许节目修正标题/描述而不触发重复 LLM 调用。
        """
        guid = str(item.get("guid") or item.get("link") or item.get("title") or "").strip()
        if not guid:
            raise ValueError("episode 缺少 GUID、链接和标题，无法去重")
        stamp = now_iso()
        metadata = dict(item)
        # metadata 中的字段都是 RSS 外部数据，序列化失败时只保留基本字段。
        try:
            import json
            metadata_json = json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError):
            metadata_json = "{}"
        with self._conn:
            self._conn.execute(
                """INSERT INTO episodes
                   (feed_url,guid,title,published,link,audio_url,metadata_json,
                    status,discovered_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'discovered',?,?)
                   ON CONFLICT(feed_url,guid) DO UPDATE SET
                     title=excluded.title, published=excluded.published,
                     link=excluded.link, audio_url=excluded.audio_url,
                     metadata_json=excluded.metadata_json""",
                (
                    feed_url, guid, str(item.get("title") or ""),
                    str(item.get("published") or ""), str(item.get("link") or ""),
                    str(item.get("audio_url") or ""), metadata_json, stamp, stamp,
                ),
            )
        return self.get_episode(feed_url, guid)

    def get_episode(self, feed_url: str, guid: str):
        return self._row(self._conn.execute(
            "SELECT * FROM episodes WHERE feed_url=? AND guid=?", (feed_url, str(guid))
        ).fetchone())

    def list_episodes(self, feed_url: str | None = None, status: str | None = None):
        query = "SELECT * FROM episodes"
        args = []
        clauses = []
        if feed_url is not None:
            clauses.append("feed_url=?")
            args.append(feed_url)
        if status is not None:
            clauses.append("status=?")
            args.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY discovered_at"
        return [self._row(row) for row in self._conn.execute(query, args).fetchall()]

    def claim_episode(self, feed_url: str, guid: str, stale_after: int = 6 * 3600) -> bool:
        """原子地将一集标为 processing，成功则返回 True。

        processing 状态超过 ``stale_after`` 秒视为上次进程崩溃，可安全重试；
        其它 processing 任务会被跳过。failed 在下一轮自动重试。
        """
        guid = str(guid)
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT status, updated_at FROM episodes WHERE feed_url=? AND guid=?",
                (feed_url, guid),
            ).fetchone()
            if row is None:
                self._conn.execute("ROLLBACK")
                return False
            status = row["status"]
            if status == STATUS_SUCCESS:
                self._conn.execute("ROLLBACK")
                return False
            if status == STATUS_PROCESSING and not self._is_stale(row["updated_at"], stale_after):
                self._conn.execute("ROLLBACK")
                return False
            stamp = now_iso()
            self._conn.execute(
                "UPDATE episodes SET status=?, updated_at=?, error=NULL WHERE feed_url=? AND guid=?",
                (STATUS_PROCESSING, stamp, feed_url, guid),
            )
        return True

    @staticmethod
    def _is_stale(updated_at: str, stale_after: int) -> bool:
        try:
            # ISO format generated by now_iso; timezone offset is optional in old rows.
            parsed = time.strptime(updated_at[:19], "%Y-%m-%dT%H:%M:%S")
            age = time.time() - time.mktime(parsed)
            return age >= max(0, stale_after)
        except (TypeError, ValueError, OverflowError):
            return True

    def mark_success(self, feed_url: str, guid: str, report_path: str):
        with self._conn:
            self._conn.execute(
                """UPDATE episodes SET status=?, processed_at=?, updated_at=?,
                   report_path=?, error=NULL WHERE feed_url=? AND guid=?""",
                (STATUS_SUCCESS, now_iso(), now_iso(), report_path, feed_url, str(guid)),
            )

    def mark_failed(self, feed_url: str, guid: str, error: str):
        with self._conn:
            self._conn.execute(
                "UPDATE episodes SET status=?, updated_at=?, error=? WHERE feed_url=? AND guid=?",
                (STATUS_FAILED, now_iso(), str(error)[:4000], feed_url, str(guid)),
            )

    def close(self):
        if getattr(self, "_conn", None) is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def __del__(self):  # pragma: no cover - interpreter shutdown path
        try:
            self.close()
        except Exception:
            pass
