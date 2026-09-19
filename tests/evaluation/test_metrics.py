import math
import unittest
from knowpipe.evaluation.metrics import evaluate


class MetricsTests(unittest.TestCase):
    def test_hand_computed_precision_and_ndcg(self):
        query = {'query_id': 'alice', 'personalized': ['a', 'b'], 'baseline': ['b', 'a'], 'judgments': {'a': 3, 'b': 0}}
        report = evaluate([query, {**query, 'query_id': 'bob'}], 2)
        self.assertEqual(report['personalized']['precision_at_k'], .5)
        self.assertEqual(report['personalized']['ndcg_at_k'], 1)
        self.assertAlmostEqual(report['baseline']['ndcg_at_k'], 1 / math.log2(3))

    def test_unjudged_and_duplicate_rankings_rejected(self):
        query = {'query_id': 'alice', 'personalized': ['a'], 'baseline': ['b'], 'judgments': {'a': 1}}
        with self.assertRaises(ValueError):
            evaluate([query, {**query, 'query_id': 'bob'}], 2)
        query['judgments']['b'] = 0
        query['personalized'] = ['a', 'a']
        with self.assertRaises(ValueError):
            evaluate([query, {**query, 'query_id': 'bob'}], 2)

    def test_short_empty_results_do_not_inflate_precision(self):
        query = {'query_id': 'alice', 'personalized': ['a'], 'baseline': [], 'judgments': {'a': 1}}
        report = evaluate([query, {**query, 'query_id': 'bob'}], 5)
        self.assertEqual(report['personalized']['precision_at_k'], .2)
        self.assertEqual(report['baseline']['ndcg_at_k'], 0)
