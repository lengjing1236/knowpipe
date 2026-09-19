"""python -m knowpipe.evaluation.metrics judgments.json --k 10"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def ranking_metrics(ranking, judgments, k):
    top = ranking[:k]
    if len(set(ranking)) != len(ranking) or any(item not in judgments for item in top):
        raise ValueError('rankings must contain unique IDs and every top-K item must be judged')
    precision = sum(judgments[item] > 0 for item in top) / k
    dcg = sum((2 ** judgments[item] - 1) / math.log2(index + 2) for index, item in enumerate(top))
    ideal = sorted(judgments.values(), reverse=True)[:k]
    idcg = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
    return {'precision_at_k': precision, 'ndcg_at_k': dcg / idcg if idcg else 0.0}


def evaluate(queries, k=10):
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 100 or not isinstance(queries, list) or len(queries) < 2:
        raise ValueError('at least two independent queries and 1 <= k <= 100 are required')
    rows, seen = [], set()
    for query in queries:
        query_id = query.get('query_id')
        judgments = query.get('judgments')
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise ValueError('query_id must be nonempty and unique')
        seen.add(query_id)
        if not isinstance(judgments, dict) or not judgments or any(
                not isinstance(key, str) or type(value) is not int or not 0 <= value <= 3
                for key, value in judgments.items()):
            raise ValueError('human judgments must map document IDs to integer grades 0..3')
        row = {'query_id': query_id}
        for mode in ('personalized', 'baseline'):
            ranking = query.get(mode)
            if not isinstance(ranking, list) or not all(isinstance(item, str) for item in ranking):
                raise ValueError('rankings must be lists of document IDs')
            row[mode] = ranking_metrics(ranking, judgments, k)
        rows.append(row)
    result = {'k': k, 'query_count': len(rows), 'queries': rows,
              'judgment_scope': 'human-labeled candidate pool, not the entire corpus'}
    for mode in ('personalized', 'baseline'):
        result[mode] = {metric: sum(row[mode][metric] for row in rows) / len(rows)
                        for metric in ('precision_at_k', 'ndcg_at_k')}
    result['difference'] = {metric: result['personalized'][metric] - result['baseline'][metric]
                            for metric in ('precision_at_k', 'ndcg_at_k')}
    return result


def main():
    parser = argparse.ArgumentParser(description='Compare rankings using human judgments (0 irrelevant, 1–3 relevant)')
    parser.add_argument('judgments', type=Path)
    parser.add_argument('--k', type=int, default=10)
    args = parser.parse_args()
    try:
        report = evaluate(json.loads(args.judgments.read_text()), args.k)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
