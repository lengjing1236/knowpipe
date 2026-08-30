"""brain.py — 大脑层抽象（所有 LLM 调用点）。

三个 provider，可随时切换：
  openai    真实 LLM API（OpenAI 兼容）。环境变量：
            KNOWPIPE_OPENAI_BASE_URL / KNOWPIPE_OPENAI_API_KEY / KNOWPIPE_OPENAI_MODEL
  manual    从 --manual-dir 读取预生成判定 JSONL（用于"我(Agent)当大脑"的演示 / 复现）
  heuristic 零依赖启发式（离线兜底，开箱即跑，质量较低）
  auto      有 API key → openai，否则 heuristic
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "gpt-4o-mini"


class BrainError(Exception):
    pass


def _read_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _post_json(url, payload, timeout=120, api_key="", retries=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if retries is None:
        try:
            retries = max(0, int(os.getenv("KNOWPIPE_LLM_RETRIES", "2")))
        except ValueError:
            retries = 2
    attempts = retries + 1
    retryable_status = {408, 409, 425, 429}
    last_error = None
    for attempt in range(attempts):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in retryable_status and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < retries:
            # 退避上限 8 秒，避免临时限流时瞬间重复轰炸网关。
            time.sleep(min(8, 2 ** attempt))
    raise last_error


# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------

DECOMPOSE_SYSTEM = """你是"知识精炼器"。把用户给的一段内容分解成"原子知识卡"。

规则：
1. 每条卡是一个可独立成立的最小断言/概念，用一句话表达。
2. 必须用自己的话复述，不得照抄原文长句；专业术语可保留原文（中英皆可）。
3. 默认用中文表达（除非术语本身是英文）。
4. 只提取内容真正主张的信息；不提取元信息（如"本文介绍了…"）。
5. 数量：内容里有多少个独立知识点就抽多少条，不要合并，也不要凑数。
6. 只输出一个 JSON 数组，不要任何其他文字。
每项结构：{"claim": "...", "topic": "主题标签", "certainty": "fact|principle|opinion", "evidence": "原文支撑片段(可选,≤80字)"}"""


CLASSIFY_SYSTEM = """你是"个人知识差分器"。用户的个人记忆库里已有若干"已知知识卡"。现在给出若干新提取的知识卡，请判断每张相对记忆库属于哪一类：
- new：全新，记忆库中没有近似概念
- known：已被某张已知卡覆盖（用户已懂）
- refine：是对某张已知卡的深化/补充/更正（不是全新，也不完全等同）
- contradict：与某张已知卡冲突，需要用户裁决

输出一个 JSON 数组，顺序与输入一致，每项：
{"index": 0, "verdict": "new|known|refine|contradict", "confidence": 0.0~1.0, "against_id": "记忆卡id或null", "reason": "一句话说明判断依据"}
只输出 JSON，不要任何其他文字。"""


SUMMARIZE_SYSTEM = """用 3~5 句话概括这段内容的核心要点，覆盖最重要的信息；用自己的话，中文表达（术语可保留英文）。只输出概括文本，不要标题和编号。"""


def _extract_json(text: str):
    """从模型输出里稳健地取出第一个 JSON 数组/对象。"""
    text = text.strip()
    # 去掉可能包裹的 ```json ``` 代码块
    text = re.sub(r"(?s)^```(?:json)?\s*", "", text)
    text = re.sub(r"(?s)\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise BrainError(f"无法解析模型输出为 JSON：{text[:200]}")


class Brain:
    def __init__(self, provider="auto", manual_dir=None,
                 base_url=None, api_key=None, model=None):
        self.provider = provider
        self.manual_dir = manual_dir
        self.base_url = (base_url
                         or os.getenv("KNOWPIPE_OPENAI_BASE_URL")
                         or os.getenv("OPENAI_BASE_URL")
                         or os.getenv("OPENAI_API_BASE")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key = (api_key
                        or os.getenv("KNOWPIPE_OPENAI_API_KEY", "")
                        or os.getenv("OPENAI_API_KEY", ""))
        self.model = (model
                      or os.getenv("KNOWPIPE_OPENAI_MODEL", "")
                      or os.getenv("KNOWPIPE_LLM_MODEL", "")
                      or os.getenv("OPENAI_MODEL", "")
                      or DEFAULT_MODEL)
        if self.provider == "auto":
            self.provider = "openai" if self.api_key else "heuristic"
        if self.provider == "manual" and not self.manual_dir:
            raise BrainError("manual provider 需要提供 --manual-dir")

    # ---------------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------------
    def decompose_chunk(self, chunk, input_id, chunk_index):
        """返回 [{claim, topic, certainty, evidence}, ...]"""
        if self.provider == "openai":
            return self._decompose_openai(chunk)
        if self.provider == "manual":
            return self._decompose_manual(input_id, chunk_index)
        return self._decompose_heuristic(chunk)

    def classify_cards(self, cards, candidates_map, input_id):
        """candidates_map: {card_index: [(claim, meta, score), ...]}
        返回 [{index, verdict, confidence, against_id, reason}, ...]"""
        if self.provider == "openai":
            return self._classify_openai(cards, candidates_map)
        if self.provider == "manual":
            return self._classify_manual(input_id, cards)
        return self._classify_heuristic(cards, candidates_map)

    def summarize(self, text, input_id):
        """对整篇内容生成概括（"防回音壁"通道）。"""
        if self.provider == "openai":
            return self._chat(SUMMARIZE_SYSTEM, text[:12000], temperature=0.3).strip()
        if self.provider == "manual":
            p = os.path.join(self.manual_dir, f"{input_id}.summary.txt")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read().strip()
            return ""
        return ""

    # ---------------------------------------------------------------
    # 文章级模式（ASR预清洗 + 知识总结文章，不抽原子卡）
    # ---------------------------------------------------------------
    def clean_transcript(self, text):
        """ASR 预清洗：术语纠错、去口误、补标点、修复断句。返回干净文稿。"""
        if self.provider != "openai":
            return text
        system = """你是技术视频逐字稿校对专家。用户给你一段语音识别（ASR）生成的原始逐字稿，
可能包含以下问题：
1. 技术术语识别错误（如 "callsoon"→"call_soon"，"redwell"→"RedVal"，"下落线"→"__"，"点下落线"→"."，"co-routine"→"coroutine"）
2. 英文单词、代码标识符、函数名、类名、字节码指令识别错误
3. 口误、重复、"嗯""啊""那个"等语气词
4. 断句错误、标点缺失
5. 同音字错误（根据上下文推断）

任务：输出校对后的干净文稿。
规则：
1. 只修正识别错误和表达问题，不改变原意、不增删技术结论
2. 专业术语、代码标识符、函数名必须准确（根据上下文和技术常识推断）
3. 补全标点，修复断句，使文本可读
4. 去掉口误、重复、语气词
5. 保留说话人的讲解顺序和逻辑结构
6. 直接输出干净文稿，不要加任何说明、注释、标题或标记"""
        return self._chat(system, text[:16000], temperature=0.1, max_tokens=8000).strip()

    def generate_article(self, clean_text):
        """把干净文稿整理成一篇结构清晰、逻辑连贯的知识总结文章。
        要求：按逻辑分段、专业名词补解释、断裂逻辑补串联。"""
        if self.provider != "openai":
            return clean_text
        system = """你是技术知识整理专家。用户给你一段校对后的技术视频文稿，
你需要把它整理成一篇结构清晰、逻辑连贯的知识总结文章。

任务要求：
1. 按照视频讲解的逻辑顺序组织内容，用小标题分段（## 级别）
2. 对专业名词、技术术语、代码概念，在第一次出现时用括号补充简短解释
   （如 "事件循环（Event Loop：持续调度就绪任务的执行器）"）
3. 如果原文逻辑有断裂、跳跃或难懂的地方，用过渡句补充串联，使逻辑连贯
   （但不要编造原文没有的结论；补充的过渡性解释自然融入正文，不标记）
4. 保留代码示例、函数名、字节码指令等技术细节，确保准确
5. 文章末尾加一个"## 核心要点"小节，用 3-5 条 bullet 总结视频最关键的知识
6. 用中文写作，术语保留英文原文
7. 直接输出文章正文（从第一个 ## 小标题开始），不要加"以下是总结"之类的元说明
8. 顶部不需要 # 一级标题（调用方会加）"""
        return self._chat(system, clean_text[:16000], temperature=0.3, max_tokens=8000).strip()

    def generate_article_from_transcript(self, transcript):
        """从 ASR 原稿直接生成知识文章，合并清洗与整理两次 LLM 调用。"""
        if self.provider != "openai":
            # 离线/manual provider 没有额外模型调用；用抽取式短摘要避免把整段
            # ASR 原稿原样写入最终报告。
            sentences = self._split_sentences(self.clean_transcript(transcript))
            if not sentences:
                return "## 核心内容\n\n（未提取到可总结的内容）"
            picked = sentences if len(sentences) <= 5 else sentences[:3] + sentences[-2:]
            body = "\n\n".join(picked)
            bullets = "\n".join(f"- {s}" for s in picked[:5])
            return f"## 核心内容\n\n{body}\n\n## 核心要点\n\n{bullets}"
        system = """你是技术知识整理专家。输入是一段可能有 ASR 识别错误的技术视频逐字稿。
请先在内部完成术语纠错、代码标识符纠正、去口误/重复、补标点和断句；然后直接输出一篇结构清晰、逻辑连贯的知识总结文章。不要输出校对过程，也不要输出逐字稿原文。

要求：
1. 按视频讲解的逻辑顺序组织内容，用 ## 小标题分段。
2. 专业名词第一次出现时用括号补充简短解释（术语保留英文原文）。
3. 对原文跳跃处补充必要的过渡句，但不得编造原文没有的结论。
4. 保留代码示例、函数名、字节码指令等技术细节并确保准确。
5. 文章末尾加“## 核心要点”小节，用 3-5 条 bullet 总结关键知识。
6. 用中文写作；直接从第一个 ## 小标题开始，不要标题、前言或其它元说明。"""
        return self._chat(system, transcript[:20000], temperature=0.25, max_tokens=8000).strip()

    # ---------------------------------------------------------------
    # openai provider
    # ---------------------------------------------------------------
    def _chat(self, system, user, temperature=0.2, max_tokens=4000):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = _post_json(self.base_url + "/chat/completions", payload, api_key=self.api_key)
        return resp["choices"][0]["message"]["content"]

    def _decompose_openai(self, chunk):
        out = self._chat(DECOMPOSE_SYSTEM, chunk)
        cards = _extract_json(out)
        if not isinstance(cards, list):
            raise BrainError("decompose 结果不是数组")
        normalized = []
        for c in cards:
            if not isinstance(c, dict):
                continue
            claim = str(c.get("claim", "")).strip()
            if not claim:
                continue
            certainty = c.get("certainty", "fact")
            if certainty not in ("fact", "principle", "opinion"):
                certainty = "fact"
            normalized.append({
                "claim": claim,
                "topic": str(c.get("topic", "")).strip(),
                "certainty": certainty,
                "evidence": str(c.get("evidence", "")).strip()[:80],
            })
        return normalized

    def _classify_openai(self, cards, candidates_map):
        known_block = []
        for i, c in enumerate(cards):
            cands = candidates_map.get(i, [])
            known_block.append(
                f"--- 待判定卡[{i}]: {c['claim']}\n    候选记忆(仅参考): "
                + ("; ".join(f"[{m['id']}]{m['claim']}" for m, s in cands[:3]) or "无")
            )
        user = "记忆库已知知识卡（判定基准）：\n" + "\n".join(
            f"- [{m['id']}] {m['claim']}" for i in candidates_map
            for m, s in candidates_map[i][:1]
        )
        user += "\n\n" + "\n".join(known_block)
        user += "\n\n请输出 JSON 数组判定。"
        out = self._chat(CLASSIFY_SYSTEM, user)
        verdicts = _extract_json(out)
        return self._normalize_verdicts(verdicts, len(cards))

    @staticmethod
    def _normalize_verdicts(verdicts, n):
        if not isinstance(verdicts, list):
            raise BrainError("classify 结果不是数组")
        out = []
        allowed = {"new", "known", "refine", "contradict"}
        for i in range(n):
            v = next((x for x in verdicts if x.get("index") == i), None)
            if not v:
                raise BrainError(f"classify 结果缺少 index={i}")
            verdict = v.get("verdict", "new")
            if verdict not in allowed:
                raise BrainError(f"classify verdict 无效: {verdict!r}")
            try:
                confidence = float(v.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            out.append({
                "index": i,
                "verdict": verdict,
                "confidence": max(0.0, min(1.0, confidence)),
                "against_id": v.get("against_id"),
                "reason": str(v.get("reason", "")),
            })
        return out

    # ---------------------------------------------------------------
    # manual provider（人工/Agent 预生成的判定）
    # ---------------------------------------------------------------
    def _decompose_manual(self, input_id, chunk_index):
        rows = _read_jsonl(os.path.join(self.manual_dir, f"{input_id}.decompose.jsonl"))
        for row in rows:
            if row.get("chunk_index") == chunk_index:
                return row.get("cards", [])
        return []

    def _classify_manual(self, input_id, cards):
        rows = _read_jsonl(os.path.join(self.manual_dir, f"{input_id}.classify.jsonl"))
        return self._normalize_verdicts([r for r in rows if "verdict" in r], len(cards))

    # ---------------------------------------------------------------
    # heuristic provider（零依赖离线兜底）
    # ---------------------------------------------------------------
    @staticmethod
    def _split_sentences(text):
        parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
        return [p.strip() for p in parts if len(p.strip()) > 12]

    def _decompose_heuristic(self, chunk):
        cards = []
        for s in self._split_sentences(chunk):
            if re.search(r"(本文|本文档|这篇文章|我们(将|会)|首先|最后|综上|此外|同时)", s) and len(s) < 60:
                continue
            cards.append({
                "claim": s[:120],
                "topic": "",
                "certainty": "fact",
                "evidence": "",
            })
        return cards

    def _classify_heuristic(self, cards, candidates_map):
        out = []
        for i, c in enumerate(cards):
            cands = candidates_map.get(i, [])
            best = max((s for _, s in cands), default=0.0)
            if best >= 0.42:
                verdict, cid = "known", cands[0][0]["id"]
            elif best >= 0.28:
                verdict, cid = "refine", cands[0][0]["id"]
            else:
                verdict, cid = "new", None
            out.append({
                "index": i, "verdict": verdict,
                "confidence": round(min(0.99, best + 0.3), 2) if verdict != "new" else 0.5,
                "against_id": cid,
                "reason": f"启发式：与记忆库最高相似度 {best:.2f}",
            })
        return out
