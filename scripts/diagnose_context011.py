#!/usr/bin/env python3
"""Post-observation development diagnostics; never held-out MVP acceptance."""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.recommendations.engine import _context_distance, PARAMETERS
from knowpipe.recommendations.semantic import LocalSemantic, SEMANTIC_VERSION
from knowpipe.recommendations.semantic_analysis import sentences, substantive
from knowpipe.recommendations.text import paragraphs, document_key, tokenize


def evidence(document, start, end):
    return {key: document[key] for key in ('source', 'doc_id', 'title')} | {
        'start': start, 'end': end, 'text': document['body_text'][start:end],
        'body_sha256': hashlib.sha256(document['body_text'].encode()).hexdigest()}


def prepare():
    import pyarrow.parquet as pq
    selected = {}
    for line in Path('state/feature011/evaluation/controlled-documents.jsonl').read_text().splitlines():
        doc = json.loads(line)
        if doc['doc_id'] in ('18/transaction-iso.html', '18/mvcc-serialization-failure-handling.html',
                             'library/asyncio-sync.html'):
            selected[doc['doc_id']] = doc
    prior = json.loads(Path('evidence/011-mvp-recommendation-validation/current-dev-partial-argos.json').read_text())
    pg = next(case for case in prior['cases'] if case['id'] == 'postgres_retry_partial')
    reviewed = pg['result']['items'][0]['comparison']['additional_evidence'][1]
    old, new = selected[reviewed['history']['doc_id']], selected[reviewed['candidate']['doc_id']]
    old_part = next(part for part in paragraphs(document_key(old['source'], old['doc_id']), 'debug', old['body_text'])
                    if part['start'] <= reviewed['history']['start'] < part['end'])
    new_part = next(part for part in paragraphs(document_key(new['source'], new['doc_id']), 'debug', new['body_text'])
                    if part['start'] <= reviewed['candidate']['start'] < part['end'])
    new_part.update({key: new[key] for key in ('source', 'doc_id', 'title')}, doc_key=document_key(new['source'], new['doc_id']), content_version='debug')
    new_unit = next(unit for unit in sentences(new_part) if unit['start'] == reviewed['candidate']['start'])
    comparison = {'old_sentence': evidence(old, reviewed['history']['start'], reviewed['history']['end']),
                  'history_context': evidence(old, old_part['start'], old_part['end']),
                  'candidate_focus': evidence(new, reviewed['candidate']['start'], reviewed['candidate']['end']),
                  'candidate_used_context': evidence(new, new_unit['hypothesis_context']['start'], new_unit['hypothesis_context']['end']),
                  'candidate_context_dependent': new_unit['dependent']}
    py = next(case for case in prior['cases'] if case['id'] == 'python_semaphore_partial')
    manifest = next(Path('state/feature011/evaluation/controlled-index').glob('*/index.json'))
    idx = json.loads(manifest.read_text())['index_path']
    idf = {row['term']: row['idf'] for row in pq.read_table(str(Path(idx) / 'terms')).to_pylist()}
    source = selected['library/asyncio-sync.html']
    parts = [part for part in paragraphs(document_key(source['source'], source['doc_id']), 'debug', source['body_text']) if substantive(part['text'])]
    branches = [Counter(tokenize(value)) for value in py['result']['query']['variants']]
    queries = []
    for branch in branches:
        weights = {term: (1 + math.log(count)) * idf[term] for term, count in branch.items() if term in idf}
        norm = math.sqrt(sum(value * value for value in weights.values()))
        queries.append({term: value / norm for term, value in weights.items()})
    lexical = []
    for part in parts:
        weights = {term: (1 + math.log(count)) * idf[term] for term, count in Counter(part['tokens']).items()}
        norm = math.sqrt(sum(value * value for value in weights.values()))
        eligible = []
        for position, query in enumerate(queries):
            score = sum(weights.get(term, 0.) / norm * weight for term, weight in query.items())
            coverage = len(set(weights).intersection(query)) / len(branches[position])
            if score >= PARAMETERS['min_relevance'] and (position == 0 or coverage >= PARAMETERS['min_query_coverage']):
                eligible.append(score)
        if eligible:
            lexical.append({**part, 'relevance': max(eligible)})
    seeds = sorted(lexical, key=lambda part: (-part['relevance'], part['pid']))[:3]
    quota = PARAMETERS['paragraphs_total'] // py['result']['semantic']['comparison_scope']['recalled_documents']
    seed_ids = {part['pid'] for part in seeds}
    expanded = sorted(parts, key=lambda part: (part['pid'] not in seed_ids,
        _context_distance(part['start'], part['end'], seeds), part['start'], part['pid']))[:quota]
    return {'kind': 'post-observation-development-diagnostic', 'is_mvp_acceptance': False,
            'postgres_context': comparison,
            'python_expansion': {'source': source['source'], 'doc_id': source['doc_id'],
                'query': py['result']['query'], 'scope': 'replay of selected-document allocation, not full recommendation evaluation',
                'paragraphs_in_document': len(parts), 'quota': quota,
                'old_seed_spans': [evidence(source, part['start'], part['end']) for part in seeds],
                'expanded_spans': [evidence(source, part['start'], part['end']) for part in expanded]}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-nli', action='store_true')
    parser.add_argument('--model', default='state/feature011/semantic-multilingual')
    args = parser.parse_args()
    root = Path('evidence/011-mvp-recommendation-validation')
    inputs = prepare()
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()
    frozen = root / 'context-diagnostic-inputs.json'
    if frozen.exists() and frozen.read_bytes() != encoded:
        raise ValueError('context_diagnostic_inputs_changed')
    frozen.write_bytes(encoded)
    if not args.run_nli:
        print(hashlib.sha256(encoded).hexdigest())
        return
    pg = inputs['postgres_context']
    pairs = [
        (pg['old_sentence']['text'], pg['candidate_focus']['text']),
        (pg['history_context']['text'], pg['candidate_focus']['text']),
        (pg['history_context']['text'], pg['candidate_used_context']['text']),
    ]
    model = LocalSemantic(args.model)
    outcomes = model.infer(pairs)
    result = {'input_sha256': hashlib.sha256(encoded).hexdigest(), 'processor_id': model.processor_id,
        'rules_version': SEMANTIC_VERSION, 'is_mvp_acceptance': False,
        'pair_labels': ['v5-isolated-sentence', 'history-context-with-candidate-focus', 'v6-used-contexts'],
        'inferences': outcomes,
        'dependent_neutral_is_supplement': False,
        'scope': 'One reviewed development counterexample; not corpus performance or independent quality proof.'}
    (root / 'context-diagnostic-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    model.close()


if __name__ == '__main__':
    main()
