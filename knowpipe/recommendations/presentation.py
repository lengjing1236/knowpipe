"""Attach existing aligned Chinese context without inventing evidence translations."""
from copy import deepcopy
from ..learning.content import content_view


def localize_evidence(db, evidence, reference, cache):
    if not isinstance(evidence, dict):
        return evidence
    localized = dict(evidence)
    key = tuple(reference.get(k) for k in ('source', 'doc_id', 'content_version'))
    if not all(key):
        return localized
    if key not in cache:
        doc = db.documents.find_one({'source': key[0], 'doc_id': key[1]}, {'body_raw': 0})
        cache[key] = content_view(doc or {})
    view = cache[key]
    if view['content_version'] != key[2] or not view['chinese_ready']:
        return localized
    start, end = evidence.get('start'), evidence.get('end')
    original = view.get('original_text') or ''
    if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(original)
            or original[start:end] != evidence.get('text')):
        return localized
    if view['language'].lower().startswith('zh'):
        return {**localized, 'chinese_context': evidence['text'], 'chinese_context_scope': 'native'}
    parts = [p for p in view.get('translation_segments', [])
             if p['source_start'] < end and p['source_end'] > start]
    if parts and parts[0]['source_start'] <= start and parts[-1]['source_end'] >= end:
        translated = view.get('chinese_text') or ''
        context = translated[parts[0]['target_start']:parts[-1]['target_end']]
        # Avoid copying an entire legacy document as a supposedly concise quote.
        if context and len(context) <= 6000:
            localized.update(chinese_context=context, chinese_context_scope='aligned_translation_units')
    return localized


def localize_items(db, items):
    cache = {}
    output = deepcopy(items)
    for item in output:
        item['goal_evidence'] = localize_evidence(db, item.get('goal_evidence'), item, cache)
        evidence = item.get('history_evidence')
        if evidence:
            evidence['candidate'] = localize_evidence(db, evidence.get('candidate'), item, cache)
            evidence['history'] = localize_evidence(db, evidence.get('history'), evidence.get('history') or {}, cache)
        comparison = item.get('comparison')
        if comparison:
            for field in ('candidate', 'history'):
                part = comparison.get(field)
                if part:
                    comparison[field] = localize_evidence(db, part, item if field == 'candidate' else part, cache)
            for name in ('covered_evidence', 'additional_evidence', 'uncertain_evidence'):
                for pair in comparison.get(name, []):
                    for field in ('candidate', 'history'):
                        part = pair.get(field)
                        if part:
                            pair[field] = localize_evidence(db, part, item if field == 'candidate' else part, cache)
    return output
