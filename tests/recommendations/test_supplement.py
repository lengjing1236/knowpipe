import unittest
from types import SimpleNamespace

from knowpipe.recommendations.engine import _supplement


class SupplementPresentationTests(unittest.TestCase):
    def test_explanation_selects_additional_passage_not_most_repeated_passage(self):
        original = 'Redis persistence RDB recovery.\n\nRedis persistence AOF recovery and fsync.'
        common = SimpleNamespace(pid='common', start=0, end=31, text=original[:31], matched_terms=['redis', 'persistence'],
            right_pid='prior', relevance=.8, similarity=1., supplement_eligible=False, supplement_score=0.,
            additional_terms=[], shared_context_terms=['rdb', 'recovery'])
        start = original.index('Redis', 2)
        extra = SimpleNamespace(pid='extra', start=start, end=len(original), text=original[start:], matched_terms=['redis', 'persistence'],
            right_pid='prior', relevance=.5, similarity=.4, supplement_eligible=True, supplement_score=.3,
            additional_terms=['aof', 'fsync'], shared_context_terms=['recovery'])
        prior = SimpleNamespace(source='official', doc_id='rdb', title='RDB', content_version='frozen',
                                pid='prior', start=0, end=31, text=original[:31])
        view, eligible = _supplement([common, extra], {'prior': prior}, 1, 2)
        self.assertTrue(eligible)
        self.assertEqual(view['candidate']['text'], original[start:])
        self.assertEqual(view['comparison']['content_version'], 'frozen')
        self.assertEqual(view['goal_coverage'], 1.)
        self.assertIn('不证明', view['limitation'])
