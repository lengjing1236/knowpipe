import math
import unittest

from knowpipe.recommendations.evaluation import fixed_queries, retrieval_metrics


class EvaluationTests(unittest.TestCase):
    def test_empty_results_remain_in_macro_average_and_multiple_labels_count(self):
        result = retrieval_metrics({'q1': {'a': 1, 'b': 1}, 'q2': {'c': 1}}, {'q1': ['a', 'x', 'b']}, k=3)
        ideal = 1 + 1 / math.log2(3)
        self.assertAlmostEqual(result['summary']['nDCG@3'], (1 + .5) / ideal / 2)
        self.assertEqual(result['summary']['Recall@3'], .5)
        self.assertEqual(result['summary']['MRR@3'], .5)
        self.assertEqual(result['summary']['empty'], .5)
        self.assertEqual(result['summary']['queries'], 2)

    def test_self_hits_and_duplicate_outputs_cannot_inflate_metrics(self):
        result = retrieval_metrics({'q': {'a': 1, 'b': 1}}, {'q': ['q', 'a', 'a', 'x']}, k=3)
        self.assertEqual(result['summary']['Recall@3'], .5)
        self.assertEqual(result['summary']['Precision@3'], 1 / 3)
        self.assertEqual(result['summary']['returned'], 2)

    def test_cohort_does_not_depend_on_order_or_query_text(self):
        rows = [{'_id': str(i), 'text': 'before'} for i in range(100)]
        ids = [r['_id'] for r in fixed_queries(rows)]
        self.assertEqual(len(ids), 32)
        changed = [{'_id': row['_id'], 'text': 'changed'} for row in reversed(rows)]
        self.assertEqual(ids, [r['_id'] for r in fixed_queries(changed)])

    def test_invalid_labels_cannot_be_silently_reinterpreted(self):
        with self.assertRaises(ValueError):
            retrieval_metrics({'q': {'a': 2}}, {})
        with self.assertRaises(ValueError):
            retrieval_metrics({'q': {'a': 0}}, {})
