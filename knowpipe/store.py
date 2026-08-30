"""store.py — 个人记忆库：原子知识卡的持久化（JSONL）+ 纯 Python 向量候选召回。"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import re
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

    def candidates_for(self, text, top_k=4):
        """返回 [(memory_card_meta, score), ...]，score 为相似度。"""
        # 索引在一次 pipeline 中会被数十/数百张卡重复查询；缓存后避免每张卡
        # 都重新扫描并构建整个记忆库。新增卡时在 add_new 中失效，保证结果正确。
        if self._index is None:
            self._index = Index()
            for c in self.cards:
                self._index.add(c["claim"], c)
        return self._index.query(text, top_k=top_k)

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
                relates_to=None, evidence=""):
        cid = self.id_of(claim)
        existing = self._touch(cid)
        if existing:
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
        self.save()
        return card

    def touch_known(self, cid):
        """命中已知卡：加计数，不重复入库。"""
        return self._touch(cid)

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
