import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from acceptance_replays011 import decision_check


class ReplayDecisionTests(unittest.TestCase):
    def check(self, **changes):
        values = {'expected': False, 'job_status': 'empty', 'semantic': {'status': 'ready'},
                  'current_job': True, 'eligible': False, 'chinese_ready': False,
                  'notice_count': 0, 'current_notice_count': 0, 'idempotent': True}
        values.update(changes)
        return decision_check(**values)

    def test_failed_worker_without_notifications_cannot_pass_negative(self):
        self.assertFalse(self.check(job_status='failed')['passed'])
        self.assertFalse(self.check(semantic={'status': 'failed'})['passed'])
        self.assertFalse(self.check(current_job=False)['passed'])

    def test_partial_unsupported_comparison_is_safe_but_not_verified_negative(self):
        result = self.check(semantic={'status': 'partial', 'error_code': 'semantic_units_unsupported'})
        self.assertTrue(result['behavior_matches'])
        self.assertFalse(result['passed'])

    def test_all_notifications_suppressed_cannot_pass_positive(self):
        self.assertFalse(self.check(expected=True)['passed'])

    def test_actual_positive_needs_current_chinese_notice_and_idempotency(self):
        values = {'expected': True, 'job_status': 'ready', 'eligible': True, 'chinese_ready': True,
                  'notice_count': 1, 'current_notice_count': 1}
        self.assertTrue(self.check(**values)['passed'])
        self.assertFalse(self.check(**{**values, 'chinese_ready': False})['passed'])
        self.assertFalse(self.check(**{**values, 'current_notice_count': 0})['passed'])
        self.assertFalse(self.check(**{**values, 'idempotent': False})['passed'])

    def test_completed_negative_decision_can_pass(self):
        self.assertTrue(self.check()['passed'])
