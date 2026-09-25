import importlib.util
from pathlib import Path
import unittest


_script = Path(__file__).resolve().parents[2] / 'scripts' / 'evaluate_mvp011.py'
_spec = importlib.util.spec_from_file_location('evaluate_mvp011', _script)
evaluation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluation)


class MVPSourceEvaluationTests(unittest.TestCase):
    def case(self, **changes):
        case = {'id': 'c', 'history': ['docs:history'], 'reference_relevant': ['docs:answer'],
                'explicit_distractors': ['docs:wrong'],
                'expected': {'top3_reference_or_fact_equivalent_required': True,
                             'personal_supplement_supported': True}}
        case.update(changes)
        return {'cases': [case]}

    def output(self, items):
        return {'cases': [{'id': 'c', 'result': {'items': items}}]}

    def test_reference_id_hit_cannot_substitute_for_source_review(self):
        report = evaluation.score_results(self.case(), self.output([{'doc_key': 'docs:answer'}]))
        self.assertTrue(report['cases'][0]['reference_top3_hit'])
        self.assertFalse(report['cases'][0]['direct_top3_confirmed'])
        self.assertIsNone(report['judged_top3_direct_rate'])
        self.assertEqual(report['counts']['top3_unjudged'], 1)
        self.assertEqual(report['status'], 'evidence_incomplete')

    def test_rejecting_all_candidates_cannot_pass_positive_supplement(self):
        report = evaluation.score_results(self.case(), self.output([]))
        self.assertTrue(report['cases'][0]['positive_supplement_required'])
        self.assertFalse(report['cases'][0]['positive_supplement_confirmed'])
        self.assertFalse(report['cases'][0]['direct_top3_confirmed'])

    def test_known_wrong_object_counts_as_false_supplement(self):
        report = evaluation.score_results(self.case(), self.output([
            {'doc_key': 'docs:wrong', 'supplement_eligible': True}]))
        self.assertEqual(report['counts']['false_supplement'], 1)
        self.assertEqual(report['judged_top3_direct_rate'], 0)

    def test_cold_start_cannot_claim_personal_supplement(self):
        manifest = self.case(history=[], expected={'must_not_claim_supplement': True})
        report = evaluation.score_results(manifest, self.output([
            {'doc_key': 'docs:answer', 'supplement_eligible': True}]))
        self.assertEqual(report['counts']['false_supplement'], 1)

    def test_mismatched_or_out_of_bounds_original_span_is_rejected(self):
        self.assertTrue(evaluation.valid_span({'start': 0, 'end': 4, 'text': '真实原文'}, '真实原文材料'))
        self.assertFalse(evaluation.valid_span({'start': 0, 'end': 8, 'text': '真实原文'}, '真实原文'))
        self.assertFalse(evaluation.valid_span({'start': 0, 'end': 4, 'text': '编造证据'}, '真实原文'))

    def test_empty_counterexample_is_not_assumed_for_other_cases(self):
        report = evaluation.score_results(self.case(expected={'empty_required': True}), self.output([]))
        self.assertTrue(report['cases'][0]['empty_check'])
        report = evaluation.score_results(self.case(), self.output([]))
        self.assertIsNone(report['cases'][0]['empty_check'])

    def test_correct_main_supplement_does_not_hide_false_additional_claim(self):
        item = {'doc_key': 'docs:answer', 'supplement_eligible': True,
                'comparison': {'additional_evidence': [{}, {}]}}
        review = {'case_id': 'c', 'doc_key': 'docs:answer', 'relevance': 'direct', 'supplement': 'supported',
                  'claims': [{'claim_index': 0, 'judgment': 'supported'},
                             {'claim_index': 1, 'judgment': 'unsupported'}]}
        report = evaluation.score_results(self.case(), self.output([item]), [review])
        self.assertTrue(report['cases'][0]['positive_supplement_confirmed'])
        self.assertEqual(report['counts']['false_supplement'], 1)

    def test_additional_claims_need_source_review_even_with_correct_document(self):
        item = {'doc_key': 'docs:answer', 'supplement_eligible': True,
                'comparison': {'additional_evidence': [{}]}}
        review = {'case_id': 'c', 'doc_key': 'docs:answer', 'relevance': 'direct', 'supplement': 'supported'}
        report = evaluation.score_results(self.case(), self.output([item]), [review])
        self.assertEqual(report['counts']['supplement_claims_pending'], 1)
        self.assertEqual(report['status'], 'evidence_incomplete')
