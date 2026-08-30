"""store.py — 个人记忆库：原子知识卡的持久化（JSONL）+ 纯 Python 向量候选召回。"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import re
import sqlite3
import time

# --------------------------------------------------------------------------
# 轻量向量：字符 n-gram + 词 token 的 TF-IDF，零第三方依赖。
# 只做"候选召回"（粗筛 top-k），语义判定交给大脑（LLM）。
# --------------------------------------------------------------------------


def _tokens(text: str, n: int = 3):
    text = text.lower()
    grams = [text[i:i + n] for i in range(len(text) - n + 1)]
    words = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text)
    return grams + words


def _term_freqs(tokens) -> dict:
    d = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def _vector_cosine(a, b):
    """计算两个 dense embedding 的余弦相似度；维度不一致时返回 0。"""
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return 0.0
    if not a or len(a) != len(b):
        return 0.0
    try:
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        na = math.sqrt(sum(float(x) ** 2 for x in a))
        nb = math.sqrt(sum(float(y) ** 2 for y in b))
    except (TypeError, ValueError):
        return 0.0
    return dot / (na * nb) if na and nb else 0.0


class Index:
    def __init__(self):
        self._docs = []          # (text, meta)
        self._vecs = []          # tf dict
        self._df = collections.Counter()
        self._weighted = None    # 缓存稳定索引的 TF-IDF 权重

    def add(self, text, meta=None):
        tf = _term_freqs(_tokens(text))
        self._docs.append((text, meta))
        self._vecs.append(tf)
        for t in tf:
            self._df[t] += 1
        self._weighted = None

    def _tfidf(self, tf):
        n = max(1, len(self._docs))
        vec = {}
        for t, c in tf.items():
            vec[t] = c * math.log(1 + n / (1 + self._df[t]))
        return vec

    @staticmethod
    def _cosine(a, b):
        inter = set(a) & set(b)
        if not inter:
            return 0.0
        dot = sum(a[t] * b[t] for t in inter)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def query(self, text, top_k=5):
        q = self._tfidf(_term_freqs(_tokens(text)))
        if self._weighted is None:
            self._weighted = [self._tfidf(v) for v in self._vecs]
        scored = []
        for v, (_doc_text, meta) in zip(self._weighted, self._docs):
            scored.append((self._cosine(q, v), meta))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(meta, s) for s, meta in scored[:top_k]]


# --------------------------------------------------------------------------
# 记忆库
# --------------------------------------------------------------------------

STATUS_NEW = "new"            # 已浮出、待用户评分
STATUS_KNOWN = "known"        # 用户确认早已知 / 被过滤
STATUS_LEARNED = "learned"    # 用户确认学到
STATUS_REFINED = "refined"    # 对某卡的深化/补充/更正（也需浮出）
STATUS_CONFLICT = "conflict"  # 与某卡冲突，待裁决

VALID_STATUSES = {STATUS_NEW, STATUS_KNOWN, STATUS_LEARNED, STATUS_REFINED, STATUS_CONFLICT}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class MemoryStore:
    def __init__(self, path: str):
        self.path = path
        self.cards = []
        self._index = None
        self._semantic_vectors = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.cards.append(json.loads(line))

    def save(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for c in self.cards:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    # ---------- 查询 ----------
    @staticmethod
    def id_of(claim: str) -> str:
        return "c_" + hashlib.sha1(claim.strip().encode("utf-8")).hexdigest()[:12]

    def get(self, cid):
        for c in self.cards:
            if c["id"] == cid:
                return c
        return None

    def by_status(self, status):
        return [c for c in self.cards if c.get("status") == status]

    def candidates_for(self, text, top_k=4, embedder=None):
        """返回 [(memory_card_meta, score), ...]，score 为相似度。"""
        # 索引在一次 pipeline 中会被数十/数百张卡重复查询；缓存后避免每张卡
        # 都重新扫描并构建整个记忆库。新增卡时在 add_new 中失效，保证结果正确。
        if self._index is None:
            self._index = Index()
            for c in self.cards:
                self._index.add(c["claim"], c)
        lexical = self._index.query(text, top_k=max(top_k * 4, top_k))
        if not embedder or not self.cards:
            return lexical[:top_k]

        # embedding 是可选增强；接口、模型或网络异常时保留 TF-IDF 结果。
        try:
            missing = [card for card in self.cards if card["id"] not in self._semantic_vectors]
            vectors = embedder([text] + [card["claim"] for card in missing])
            if not vectors or vectors[0] is None:
                return lexical[:top_k]
            query_vector = vectors[0]
            for card, vector in zip(missing, vectors[1:]):
                if vector is not None:
                    self._semantic_vectors[card["id"]] = vector
            lexical_scores = {card["id"]: score for card, score in lexical}
            ranked = []
            for card in self.cards:
                vector = self._semantic_vectors.get(card["id"])
                if vector is None:
                    continue
                semantic = _vector_cosine(query_vector, vector)
                lexical_score = lexical_scores.get(card["id"], 0.0)
                score = 0.35 * lexical_score + 0.65 * max(0.0, semantic)
                if score > 0:
                    ranked.append((card, score))
            if ranked:
                ranked.sort(key=lambda item: item[1], reverse=True)
                return ranked[:top_k]
        except Exception:  # noqa: BLE001 - 检索增强失败不应阻断主流程
            pass
        return lexical[:top_k]

    def stats(self):
        counts = collections.Counter(c.get("status", "?") for c in self.cards)
        return {
            "total": len(self.cards),
            "by_status": dict(counts),
            "path": self.path,
        }

    # ---------- 写入 ----------
    def _touch(self, cid):
        for c in self.cards:
            if c["id"] == cid:
                c["last_seen"] = now_iso()
                c["seen_count"] = c.get("seen_count", 0) + 1
                return c
        return None

    def add_new(self, claim, source, topic="", certainty="fact", status=STATUS_NEW,
                relates_to=None, evidence="", persist=True):
        """新增知识卡。

        ``persist=False`` 用于一次 pipeline 的批量写入：调用方完成整批处理后
        再显式 ``save()``，避免每张卡都重写整个 JSONL 文件。默认仍立即持久化，
        保持 seed/review 等旧调用行为不变。
        """
        cid = self.id_of(claim)
        existing = self._touch(cid)
        if existing:
            if persist:
                self.save()
            return existing
        card = {
            "id": cid,
            "claim": claim,
            "source": source,
            "topic": topic,
            "certainty": certainty,
            "status": status,
            "relates_to": relates_to,
            "seen_count": 1,
            "evidence": evidence,
            "created": now_iso(),
            "last_seen": now_iso(),
        }
        self.cards.append(card)
        self._index = None
        if persist:
            self.save()
        return card

    def touch_known(self, cid, persist=True):
        """命中已知卡：加计数，不重复入库。"""
        card = self._touch(cid)
        if card and persist:
            self.save()
        return card

    def update_status(self, cid, status, note=None):
        c = self.get(cid)
        if not c:
            return None
        c["status"] = status
        c["last_seen"] = now_iso()
        if note:
            c["review_note"] = note
        self.save()
        return c

    def delete(self, cid):
        """删除一张卡并持久化。"""
        before = len(self.cards)
        self.cards = [card for card in self.cards if card.get("id") != cid]
        if len(self.cards) == before:
            return False
        self._index = None
        self._semantic_vectors.pop(cid, None)
        self.save()
        return True


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store with the same public API as ``MemoryStore``.

    JSONL remains the default for backwards compatibility.  SQLite is useful for
    a personal library that is growing or being queried while another command is
    running: writes are transactional and the database can be indexed safely.
    """

    _COLUMNS = (
        "id", "claim", "source", "topic", "certainty", "status",
        "relates_to", "seen_count", "evidence", "created", "last_seen",
        "review_note",
    )

    def __init__(self, path: str):
        self.path = path
        self.cards = []
        self._index = None
        self._semantic_vectors = {}
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()
        self._load()

    def _ensure_schema(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                claim TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL DEFAULT '',
                certainty TEXT NOT NULL DEFAULT 'fact',
                status TEXT NOT NULL DEFAULT 'new',
                relates_to TEXT,
                seen_count INTEGER NOT NULL DEFAULT 1,
                evidence TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                review_note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
            CREATE INDEX IF NOT EXISTS idx_cards_topic ON cards(topic);
            """
        )
        self._conn.commit()

    def _load(self):
        rows = self._conn.execute(
            "SELECT id, claim, source, topic, certainty, status, relates_to, "
            "seen_count, evidence, created, last_seen, review_note "
            "FROM cards ORDER BY rowid"
        ).fetchall()
        self.cards = []
        for row in rows:
            card = dict(row)
            if card.get("review_note") is None:
                card.pop("review_note", None)
            self.cards.append(card)

    def save(self):
        """同步当前 cards 快照，整个操作在一个 SQLite 事务中完成。"""
        with self._conn:
            sql = """
                INSERT INTO cards
                (id, claim, source, topic, certainty, status, relates_to,
                 seen_count, evidence, created, last_seen, review_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  claim=excluded.claim,
                  source=excluded.source,
                  topic=excluded.topic,
                  certainty=excluded.certainty,
                  status=excluded.status,
                  relates_to=excluded.relates_to,
                  seen_count=excluded.seen_count,
                  evidence=excluded.evidence,
                  created=excluded.created,
                  last_seen=excluded.last_seen,
                  review_note=excluded.review_note
            """
            for c in self.cards:
                self._conn.execute(sql, tuple(c.get(k) for k in self._COLUMNS))

    def delete(self, cid):
        """删除一张 SQLite 卡；不重写其它进程可能新增的卡片。"""
        if not any(card.get("id") == cid for card in self.cards):
            return False
        with self._conn:
            self._conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
        self.cards = [card for card in self.cards if card.get("id") != cid]
        self._index = None
        return True

    def close(self):
        if getattr(self, "_conn", None) is not None:
            self._conn.close()
            self._conn = None

    def __del__(self):  # pragma: no cover - interpreter shutdown path
        try:
            self.close()
        except Exception:
            pass


def open_store(path: str):
    """按文件扩展名选择记忆后端。

    ``*.db``, ``*.sqlite`` 和 ``*.sqlite3`` 使用 SQLite；其它路径继续使用
    JSONL。这样已有 ``memory/cards.jsonl`` 无需迁移即可继续工作。
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return SQLiteMemoryStore(path)
    return MemoryStore(path)


def migrate_jsonl_to_sqlite(source: str, destination: str) -> int:
    """把现有 JSONL 记忆库迁移到 SQLite，返回迁移卡片数量。"""
    if os.path.abspath(source) == os.path.abspath(destination):
        raise ValueError("源文件与 SQLite 目标路径不能相同")
    source_store = MemoryStore(source)
    destination_store = SQLiteMemoryStore(destination)
    # 兼容 seed JSONL：这类文件通常只有 claim/topic/certainty，没有运行时字段。
    normalized = []
    for raw in source_store.cards:
        claim = str(raw.get("claim", "")).strip()
        if not claim:
            continue
        card = dict(raw)
        card.setdefault("id", MemoryStore.id_of(claim))
        card.setdefault("source", "import")
        card.setdefault("topic", "")
        card.setdefault("certainty", "fact")
        card.setdefault("status", STATUS_NEW)
        card.setdefault("relates_to", None)
        card.setdefault("seen_count", 1)
        card.setdefault("evidence", "")
        created = card.setdefault("created", now_iso())
        card.setdefault("last_seen", created)
        normalized.append(card)
    # 迁移语义是“目标库与源库一致”，因此显式清理目标库中的旧卡；普通
    # SQLiteMemoryStore.save() 则不会删除其它进程新增的卡片。
    with destination_store._conn:
        destination_store._conn.execute("DELETE FROM cards")
    destination_store.cards = normalized
    destination_store._index = None
    destination_store.save()
    destination_store.close()
    return len(normalized)
