"""Versioned bilingual queries and explicit, evidence-backed technology filters.

The catalogue identifies literal technology names, not user knowledge or intent.
Changing query rules invalidates recommendations but does not rebuild corpus TF-IDF.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..learning.providers import TextResult, UnconfiguredProvider, processor_identity
from ..learning.quality import CHECKS_VERSION

QUERY_VERSION = 'bilingual-object-query-v2'
# Source names are assigned by the existing official-document adapters. A URL
# alone (e.g. one mentioned inside a forum answer) cannot confer this identity.
OFFICIAL_SOURCES = {
    'python_docs': ('Python', {'docs.python.org'}),
    'django_docs': ('Django', {'docs.djangoproject.com'}),
    'postgresql_docs': ('PostgreSQL', {'www.postgresql.org', 'postgresql.org'}),
    'docker_docs': ('Docker', {'docs.docker.com'}),
}


def source_entity_evidence(source, url, entity):
    declared = OFFICIAL_SOURCES.get(source)
    if not declared or entity['name'] != declared[0] or not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'https' and parsed.hostname in declared[1]
                 and parsed.username is None and parsed.password is None and parsed.port in (None, 443))
    except ValueError:
        return None
    if not valid:
        return None
    return {'name': entity['name'], 'field': 'source_url', 'start': 0, 'end': len(url), 'text': url}


def document_matches_entity(title, body, source, url, entity):
    return bool(re.search(entity_pattern(entity), title or '') or re.search(entity_pattern(entity), body or '')
                or source_entity_evidence(source, url, entity))
CATALOGUE = (
    ('PostgreSQL', ('postgresql', 'postgres')), ('MySQL', ('mysql',)),
    ('SQLite', ('sqlite',)), ('Redis', ('redis',)), ('MongoDB', ('mongodb',)),
    ('Python', ('python',)), ('Pandas', ('pandas',)), ('NumPy', ('numpy',)),
    ('JavaScript', ('javascript',)), ('TypeScript', ('typescript',)),
    ('Java', ('java',)), ('Rust', ('rust',)), ('C++', ('c++',)),
    ('Django', ('django',)), ('Flask', ('flask',)), ('Spring', ('spring',)),
    ('React', ('react',)), ('Vue', ('vue',)), ('Node.js', ('node.js', 'nodejs')),
    ('Docker', ('docker',)), ('Kubernetes', ('kubernetes', 'k8s')),
    ('Spark', ('spark',)), ('Hadoop', ('hadoop',)), ('Kafka', ('kafka',)),
    ('Linux', ('linux',)), ('Ubuntu', ('ubuntu',)), ('AWS', ('aws', 'amazon web services')),
    ('TensorFlow', ('tensorflow',)), ('PyTorch', ('pytorch',)), ('Git', ('git',)),
)


def entity_pattern(entity):
    return '(?i)(?<![a-z0-9_])(?:' + '|'.join(re.escape(a) for a in entity['aliases']) + ')(?![a-z0-9_])'


def entities_in(original):
    entities = []
    for name, aliases in CATALOGUE:
        entity = {'name': name, 'aliases': list(aliases)}
        if re.search(entity_pattern(entity), original):
            entities.append(entity)
    for name in re.findall(r'`([^`\n]{1,80})`', original):
        # Explicit code identifiers are literal constraints, never executable text.
        if not any(name.lower() in e['aliases'] for e in entities):
            entities.append({'name': name, 'aliases': [name.lower()]})
    return entities


def entity_evidence(title, body, entities, source=None, source_url=None):
    evidence = []
    for entity in entities:
        for field, text in (('title', title or ''), ('body_text', body or '')):
            match = re.search(entity_pattern(entity), text)
            if match:
                start, end = max(0, match.start() - 45), min(len(text), match.end() + 90)
                evidence.append({'name': entity['name'], 'field': field,
                                 'start': start, 'end': end, 'text': text[start:end]})
                break
        else:
            origin = source_entity_evidence(source, source_url, entity)
            if origin:
                evidence.append(origin)
            else:
                return []  # all explicitly requested objects must be evidenced
    return evidence


def processing_identity(goal_provider, translation_provider, semantic=None):
    basis = [QUERY_VERSION, CHECKS_VERSION, processor_identity(goal_provider), processor_identity(translation_provider),
             processor_identity(semantic) if semantic is not None else 'semantic-unavailable']
    return hashlib.sha256(json.dumps(basis).encode()).hexdigest()


def original_query(original):
    return {'original': original, 'variants': [original], 'status': 'native',
            'processor_id': None, 'rules_version': QUERY_VERSION,
            'entities': entities_in(original), 'error_code': None}


def prepare_goal(db, original, provider):
    if not isinstance(original, str) or not original.strip() or len(original) > 1000:
        raise ValueError('invalid_goal')
    result = original_query(original)
    if not re.search(r'[\u4e00-\u9fff]', original):
        return result
    result['processor_id'] = processor_identity(provider)
    if isinstance(provider, UnconfiguredProvider):
        return {**result, 'status': 'unavailable', 'error_code': 'goal_translation_unavailable'}
    key = hashlib.sha256(json.dumps([original, result['processor_id'], QUERY_VERSION], ensure_ascii=False).encode()).hexdigest()
    cached = db.goal_interpretations.find_one({'_id': key})
    if cached:
        return cached['query']
    try:
        translated = provider.translate(original, 'zh', 'en')
        if (not isinstance(translated, TextResult) or not translated.complete or
                not translated.language.lower().startswith('en') or not translated.text.strip() or
                len(translated.text) > 4000 or not re.search(r'[a-zA-Z]', translated.text) or
                re.search(r'[\u4e00-\u9fff]', translated.text)):
            raise ValueError('invalid_goal_translation')
    except Exception:
        # Provider details can contain local paths; publish a finite error only.
        return {**result, 'status': 'failed', 'error_code': 'goal_translation_failed'}
    result.update(status='translated', variants=list(dict.fromkeys([original, translated.text.strip()])))
    db.goal_interpretations.update_one({'_id': key}, {'$setOnInsert': {
        'query': result, 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    return result
