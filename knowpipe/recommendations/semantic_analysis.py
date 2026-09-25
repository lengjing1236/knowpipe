"""Bounded extractive comparisons whose uncertainty remains visible."""
from __future__ import annotations

import hashlib
import re

from .semantic import EnglishText, UnsupportedSemanticInput

LIMITS = {'candidate_paragraphs': 300, 'candidate_sentences': 90,
          'history_sentences': 100, 'comparison_pairs': 270}
THRESHOLDS = {'relevance': .015, 'goal_similarity': .35, 'entailment': .75, 'contradiction': .65,
              'neutral': .70, 'context_similarity': .30, 'duplicate': .95}
LIMITATION = ('仅对本次与目标相关、版本一致的已读片段作有限比较。模型判断可能有误；'
              '候选补充不代表用户从未学过，也不证明学习收益。')


def substantive(text):
    stripped = text.strip()
    chinese = len(re.findall(r'[\u4e00-\u9fff]', stripped))
    enough_content = (chinese >= 12 and len(stripped) >= 20) if chinese else (
        len(stripped) >= 50 and len(re.findall(r'[A-Za-z][\w-]*', stripped)) >= 8)
    return (enough_content and not stripped.endswith(('?', '？'))
            and bool(re.search(r'[.!;。；]', stripped)))


def sentences(part, scope=None):
    """Literal source spans; headings/questions and incomplete fragments excluded."""
    text = part['text']
    starts = [0]
    stops = []
    for match in re.finditer(r'(?<=[.!?])\s+(?=[A-Z`])|(?<=[。！？])|\n+', text):
        stops.append(match.start())
        starts.append(match.end())
    stops.append(len(text))
    result = []
    for start, end in zip(starts, stops):
        while start < end and text[start].isspace(): start += 1
        while end > start and text[end - 1].isspace(): end -= 1
        excerpt = text[start:end]
        # Long sentences are unsupported, rather than falsely called complete
        # after truncating a condition/exception from their end.
        if len(excerpt) > 900:
            if scope is not None:
                scope['oversized_sentences'] = scope.get('oversized_sentences', 0) + 1
            continue
        if not substantive(excerpt):
            continue
        row = {key: part.get(key) for key in ('source', 'doc_id', 'title', 'content_version', 'doc_key', 'pid')}
        row.update(start=part['start'] + start, end=part['start'] + end, text=excerpt,
                   uid=hashlib.sha256(f"{part['pid']}:{start}:{end}".encode()).hexdigest())
        row['context'] = {**public_evidence(part), 'pid': part['pid']}
        # Discourse-dependent sentences are not independent factual claims.
        # Their hypothesis includes preceding source context; neutral on that
        # composite hypothesis cannot establish which sentence adds content.
        dependent = bool(re.match(r'(?i)^(?:this\b|that\b|these\b|those\b|it\b|such\b|for example\b|therefore\b|however\b|这|该|它|因此|例如)', excerpt))
        context_start = starts[max(0, starts.index(start) - 1)] if start in starts else 0
        context_start = max(0, min(context_start, start))
        row['dependent'] = dependent
        row['hypothesis_context'] = ({**public_evidence(row), 'start': part['start'] + context_start,
            'text': text[context_start:end]} if dependent else public_evidence(row))
        result.append(row)
    return result


def public_evidence(row):
    if row is None:
        return None
    return {key: row.get(key) for key in ('source', 'doc_id', 'title', 'content_version', 'start', 'end', 'text')}


def _convert(parts, converter, translate=True):
    converted, errors = [], 0
    for part in parts:
        try:
            text = converter.convert(part['text']) if translate else part['text']
        except Exception:
            errors += 1
            continue
        converted.append({**part, 'model_text': text})
    return converted, errors


def _safe_features(provider, method, values, *prefix):
    """An overlong individual unit never invalidates or truncates other units."""
    try:
        return list(zip(values, getattr(provider, method)(*prefix, values))), 0
    except UnsupportedSemanticInput:
        output, errors = [], 0
        for value in values:
            try:
                output.append((value, getattr(provider, method)(*prefix, [value])[0]))
            except UnsupportedSemanticInput:
                errors += 1
        return output, errors


def analyze(provider, query, candidate_parts, history_parts):
    """Return finite per-document features and literal evidence, without ranking."""
    import numpy as np
    extraction_scope = {'oversized_sentences': 0}
    converter = getattr(provider, 'text', None) or EnglishText()
    multilingual = getattr(provider, 'language', 'en') == 'multilingual'
    model_goal = query if multilingual else converter.convert(query)
    candidates, errors = _convert(candidate_parts[:LIMITS['candidate_paragraphs']], converter, not multilingual)
    candidate_scores, count = _safe_features(provider, 'relevance', [p['model_text'] for p in candidates], model_goal)
    errors += count
    by_text = {text: float(score) for text, score in candidate_scores}
    candidates = [{**p, 'semantic_relevance': by_text[p['model_text']]} for p in candidates
                  if p['model_text'] in by_text and by_text[p['model_text']] >= THRESHOLDS['relevance']]
    candidates.sort(key=lambda p: (-p['semantic_relevance'], p['doc_key'], p['pid']))
    documents = {}
    for part in candidates:
        documents.setdefault(part['doc_key'], {'parts': [], 'units': []})['parts'].append(part)
    units = []
    # Round-robin documents avoids spending all comparison capacity on one source.
    unit_queues = {key: [unit for part in doc['parts'] for unit in sentences(part, extraction_scope)]
                   for key, doc in documents.items()}
    available_by_document = {key: len(queue) for key, queue in unit_queues.items()}
    candidate_unit_total = sum(map(len, unit_queues.values()))
    while len(units) < LIMITS['candidate_sentences'] and any(unit_queues.values()):
        for key in unit_queues:
            if unit_queues[key] and len(units) < LIMITS['candidate_sentences']:
                units.append(unit_queues[key].pop(0))
    all_history_units = [unit for part in history_parts for unit in sentences(part, extraction_scope)]
    history_units = all_history_units[:LIMITS['history_sentences']]
    errors += extraction_scope['oversized_sentences']
    units, count = _convert(units, converter, not multilingual)
    errors += count
    history_units, count = _convert(history_units, converter, not multilingual)
    errors += count
    unit_scores, count = _safe_features(provider, 'relevance', [u['model_text'] for u in units], model_goal)
    errors += count
    scores = {text: float(score) for text, score in unit_scores}
    units = [{**u, 'semantic_relevance': scores[u['model_text']]} for u in units
             if scores.get(u['model_text'], 0.) >= THRESHOLDS['relevance']]
    # Prior material may explain the prerequisite without answering today's new
    # question. Requiring it to pass the candidate's answer-relevance gate would
    # remove precisely the foundations the user supplied for comparison.
    embedded, count = _safe_features(provider, 'encode', [model_goal, *[u['model_text'] for u in units + history_units]])
    errors += count
    vectors = {text: np.asarray(vector) for text, vector in embedded}
    if model_goal not in vectors:
        raise UnsupportedSemanticInput('semantic_goal_unsupported')
    def with_goal_similarity(unit):
        if unit['model_text'] not in vectors:
            return None
        similarity = max(0., min(1., float(np.dot(vectors[model_goal], vectors[unit['model_text']]))))
        if similarity < THRESHOLDS['goal_similarity']:
            return None
        rank_feature = unit.get('semantic_relevance', scores.get(unit['model_text'], 0.))
        # Cross-encoder sigmoid is an uncalibrated ranking feature. A second,
        # native-language signal prevents generic low-score matches gaining
        # admission merely because the first threshold was relaxed.
        return {**unit, 'cross_encoder_relevance': rank_feature, 'goal_similarity': similarity,
                'semantic_relevance': .7 * similarity + .3 * rank_feature}
    units = [row for unit in units if (row := with_goal_similarity(unit)) is not None]
    history_units = [unit for unit in history_units if unit['model_text'] in vectors]
    comparisons, pair_inputs = [], []
    history_context_count = len({(h['context']['source'], h['context']['doc_id'], h['context']['start'],
                                  h['context']['end']) for h in history_units})
    for unit in units:
        ordered = sorted([(float(np.dot(vectors[unit['model_text']], vectors[h['model_text']])), h)
                         for h in history_units], key=lambda pair: (-pair[0], pair[1]['uid']))
        nearest, seen_contexts = [], set()
        for similarity, history in ordered:
            identity = (history['context']['source'], history['context']['doc_id'],
                        history['context']['start'], history['context']['end'])
            if identity not in seen_contexts:
                nearest.append((similarity, history))
                seen_contexts.add(identity)
            if len(nearest) == 3:
                break
        for similarity, history in nearest:
            try:
                pair = (converter.convert(history['context']['text']),
                        converter.convert(unit['hypothesis_context']['text']))
            except Exception:
                errors += 1
                continue
            comparisons.append((unit, history, max(0., min(1., similarity))))
            pair_inputs.append(pair)
    inferences, count = _safe_features(provider, 'infer', pair_inputs)
    errors += count
    inferred = dict(inferences)
    compared = {}
    for (unit, history, similarity), pair in zip(comparisons, pair_inputs):
        outcome = inferred.get(pair)
        if outcome is not None:
            compared.setdefault(unit['uid'], []).append((history, similarity, outcome))
    for unit in units:
        matches = compared.get(unit['uid'], [])
        relation, chosen = 'uncertain', None
        if not history_units:
            relation = 'goal_only'
        elif matches:
            covered = [m for m in matches if m[2]['entailment'] >= THRESHOLDS['entailment']]
            contradicted = [m for m in matches if m[2]['contradiction'] >= THRESHOLDS['contradiction']]
            # Conflicting supplied evidence cannot simultaneously support an
            # unqualified supplement or a confident coverage conclusion.
            if contradicted:
                chosen = max(contradicted, key=lambda m: m[2]['contradiction'])
            elif covered:
                relation = 'covered'
                chosen = max(covered, key=lambda m: m[2]['entailment'])
            else:
                possible = [m for m in matches if m[1] >= THRESHOLDS['context_similarity']
                            and m[2]['neutral'] >= THRESHOLDS['neutral']]
                if (possible and not unit['dependent'] and max(m[2]['entailment'] for m in matches) < .35
                        and len(matches) == min(3, history_context_count)):
                    relation = 'possible_supplement'
                    chosen = max(possible, key=lambda m: (m[1], m[2]['neutral']))
                else:
                    chosen = max(matches, key=lambda m: m[1])
        history, similarity, outcome = chosen if chosen else (None, 0., {})
        novelty = (unit['semantic_relevance'] * similarity * outcome.get('neutral', 0.)
                   if relation == 'possible_supplement' else 0.)
        documents[unit['doc_key']]['units'].append({'status': relation,
            'candidate': public_evidence(unit['hypothesis_context']),
            'history': public_evidence(history['context']) if history else None,
            'focus': public_evidence(unit), 'history_focus': public_evidence(history),
            'context_dependent': unit['dependent'],
            'scores': {'relevance': unit['semantic_relevance'], 'context_similarity': similarity,
                       'cross_encoder_relevance': unit['cross_encoder_relevance'],
                       'goal_similarity': unit['goal_similarity'],
                       'supplement': novelty, **{key: float(value) for key, value in outcome.items()}}})
    output, duplicate_pairs = {}, []
    centroids = {}
    for key, doc in documents.items():
        # A relevant paragraph still needs a substantive, supported-window unit.
        # Otherwise only a title/question may be matching the requested words.
        valid_units = doc['units']
        if not valid_units:
            continue
        covered = [u for u in valid_units if u['status'] == 'covered']
        additional = [u for u in valid_units if u['status'] == 'possible_supplement']
        unknown = [u for u in valid_units if u['status'] == 'uncertain']
        uncompared = max(0, available_by_document.get(key, 0) - len(valid_units))
        if additional:
            status, selected = 'possible_supplement', max(additional, key=lambda u: u['scores']['supplement'])
        elif covered and not unknown and not uncompared:
            status, selected = 'covered', covered[0]
        elif not history_units:
            status, selected = ('goal_only' if not history_parts else 'uncertain'), valid_units[0]
        else:
            status, selected = 'uncertain', (unknown or valid_units)[0]
        overlap = len(covered) / (len(valid_units) + uncompared)
        supplement = max((u['scores']['supplement'] for u in additional), default=0.)
        comparison = {'status': status, 'candidate': selected['candidate'], 'history': selected['history'],
            'covered_evidence': covered[:3], 'additional_evidence': additional[:3],
            'uncertain_evidence': unknown[:3], 'scores': {'coverage': overlap, 'supplement': supplement},
            'scope': {'candidate_sentences': len(valid_units), 'history_sentences': len(history_units),
                      'uncompared_candidate_sentences': uncompared,
                      'nearest_history_per_sentence': 3}, 'limitation': LIMITATION}
        output[key] = {'relevance': doc['parts'][0]['semantic_relevance'], 'history_overlap': overlap,
                       'supplement_score': supplement, 'comparison': comparison,
                       'goal_evidence': selected['candidate'], 'part': doc['parts'][0],
                       'unit_features': [{'relevance': unit['scores']['relevance'],
                            'covered': float(unit['status'] == 'covered'),
                            'supplement': unit['scores']['supplement']} for unit in valid_units]}
        # A budget/model-window skip cannot turn a partially compared document
        # into a fully covered one and suppress its potentially useful content.
        output[key]['unit_features'].extend({'relevance': 0., 'covered': 0., 'supplement': 0.}
                                             for _ in range(uncompared))
        related_vectors = [vectors[u['model_text']] for u in units if u['doc_key'] == key]
        if related_vectors:
            mean = np.mean(related_vectors, axis=0)
            centroids[key] = mean / max(float(np.linalg.norm(mean)), 1e-9)
    for left in sorted(centroids):
        for right in sorted(centroids):
            if left < right:
                duplicate_pairs.append({'left_key': left, 'right_key': right,
                    'similarity': max(0., min(1., float(np.dot(centroids[left], centroids[right]))))})
    return {'documents': output, 'pairs': duplicate_pairs, 'status': 'partial' if errors else 'ready',
            'language': 'multilingual' if multilingual else 'en',
            'error_code': 'semantic_units_unsupported' if errors else None,
            'comparison_scope': {'candidate_paragraphs': len(candidate_parts),
                'candidate_sentences': len(units), 'history_sentences': len(history_units),
                'comparison_pairs': len(inferences), 'unsupported_units': errors,
                'history_contexts': history_context_count,
                'history_selection_method': 'candidate-nearest-context-not-answer-gated',
                'oversized_sentences': extraction_scope['oversized_sentences'],
                'candidate_sentences_outside_budget': max(0, candidate_unit_total - LIMITS['candidate_sentences']),
                'history_sentences_outside_budget': max(0, len(all_history_units) - LIMITS['history_sentences']),
                'limits': dict(LIMITS)}}
