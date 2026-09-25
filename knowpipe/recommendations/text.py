"""Versioned lexical normalization and evidence-preserving paragraph boundaries."""
import hashlib
import json
import logging
import re

ALGORITHM = 'goal-paragraph-coverage-mmr-v3'
STOP = set('a an the and or to of for in on with by is are be as at from it this that these those has have had can may will would should not only also more most using used use into about how what which we you our their its than then when if else do does did documentation copyright'.split())
STOP.update('的 了 和 与 或 在 对 将 是 为 把 中 也 都 而 及 可以 一个 这个 这些 以及 如何 什么 我们 你们 使用 通过 进行 有关 相关 理解 学习 了解 介绍 今天 欢迎 收听 感谢 订阅 大家 本期 播客'.split())
# Explicit, limited aliases are auditable; this is not general cross-language semantics.
ALIASES = {'持久化': 'persistence', '数据库': 'database', '事务': 'transaction', '回滚': 'rollback',
           '提交': 'commit', '并发': 'concurrency', '异步': 'async', '异常': 'exception',
           '列表': 'list', '字典': 'dictionary', '查询': 'query', '索引': 'index',
           '分布式': 'distributed', '聚类': 'cluster', '缓存': 'cache', '快照': 'snapshot',
           '恢复': 'recovery', '内存': 'memory', '日志': 'log', '复制': 'replication',
           '排序': 'sort', '队列': 'queue', '线程': 'thread', '进程': 'process',
           'transactions': 'transaction', 'databases': 'database', 'snapshots': 'snapshot',
           'queries': 'query', 'indexes': 'index', 'exceptions': 'exception', 'lists': 'list',
           'dictionaries': 'dictionary', 'threads': 'thread', 'processes': 'process',
           'asynchronous': 'async', 'concurrent': 'concurrency', 'caching': 'cache'}


def document_key(source, doc_id):
    return hashlib.sha256(json.dumps([source, doc_id], ensure_ascii=False).encode()).hexdigest()


def _chinese_words(text):
    import jieba
    jieba.setLogLevel(logging.ERROR)
    if not getattr(_chinese_words, '_ready', False):
        for term in ALIASES:
            if re.search(r'[\u4e00-\u9fff]', term):
                jieba.add_word(term, freq=100000)
        _chinese_words._ready = True
    return jieba.cut(text, HMM=False)


def tokenize(text):
    # English corpora must not load the large Chinese dictionary in every
    # Python executor. The token normalization itself is unchanged.
    result = []
    for chunk in re.findall(r'[a-zA-Z][a-zA-Z0-9_+#.-]*|[\u4e00-\u9fff]+', text.lower()):
        words = _chinese_words(chunk) if re.search(r'[\u4e00-\u9fff]', chunk) else [chunk]
        for word in words:
            word = word.strip('.-')
            if len(word) >= 2 and word not in STOP:
                result.append(ALIASES.get(word, word))
    return result


def _podcast_boilerplate(text):
    """Ignore only short blocks made entirely of known greetings/call-to-action.

    Generic stop words would also erase networking 'listening' and pub/sub
    'subscribe'. Context matching preserves those technical statements and
    mixed paragraphs containing substantive content. Original fulltext stays
    intact; only the index omits these non-substantive blocks.
    """
    if len(text) > 600:
        return False
    sentences = [part.strip().lower() for part in re.split(r'[.!?。！？]+', text) if part.strip()]
    name = r"(?:[\w+&'-]+\s+){0,8}"
    patterns = (
        r'welcome(?:\s+back)?\s+to\s+' + name + r'(?:podcast|show|episode)(?:\s+(?:everyone|everybody))?',
        r'(?:thanks|thank you)\s+for\s+(?:listening|tuning in|watching|subscribing)'
        r'(?:\s+(?:and|or)\s+(?:listening|tuning in|watching|subscribing))*'
        r'(?:\s+to\s+' + name + r'(?:podcast|show|episode|channel))?',
        r'(?:please\s+)?(?:like|subscribe|follow|share|rate)(?:\s+(?:and|or)\s+(?:like|subscribe|follow|share|rate))*',
        r'(?:please\s+)?(?:subscribe|follow)\s+(?:to\s+)?(?:our|the|this)\s+(?:podcast|show|channel)',
    )
    return bool(sentences) and all(any(re.fullmatch(pattern, sentence) for pattern in patterns) for sentence in sentences)


def paragraphs(doc_key, version, text):
    result = []
    # Preserve literal offsets. Long natural paragraphs are split near punctuation.
    for match in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text):
        start, stop = match.span()
        while start < stop:
            end = min(start + 1200, stop)
            if end < stop:
                boundaries = [m.end() for m in re.finditer(r'[。！？；.!?;\n]', text[start:end])]
                if boundaries and boundaries[-1] > 300:
                    end = start + boundaries[-1]
            block = text[start:end]
            terms = tokenize(block)
            if len(terms) >= 3 and not _podcast_boilerplate(block):
                pid = hashlib.sha256(f'{doc_key}:{version}:{start}:{end}'.encode()).hexdigest()
                result.append({'pid': pid, 'start': start, 'end': end, 'text': block, 'tokens': terms})
            start = end
    return result
