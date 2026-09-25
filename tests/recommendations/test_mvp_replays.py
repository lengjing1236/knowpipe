import importlib.util
from pathlib import Path
import sys
import unittest
from datetime import datetime, timedelta, timezone

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from acceptance_replays011 import decision_check, translation_phase


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


class ReplayTranslationWaitTests(unittest.TestCase):
    def phase(self, *, count=1, status='failed', processor='p', ready=False, expired=False):
        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        doc = {'translation_expected_processor': 'p', 'translation_attempts': {
            'source_version': 'v', 'processor_id': processor, 'count': count,
            'retry_at': now + timedelta(minutes=-1 if expired else 5)}}
        view = {'content_version': 'v', 'content_status': 'fulltext', 'chinese_ready': ready,
                'processing': {'translation': {'status': status, 'error_code': 'translation_quality_failed'}}}
        return translation_phase({'content_version': 'v'}, doc, view, now=now)['phase']

    def test_first_failure_waits_for_actual_retry_schedule(self):
        self.assertEqual(self.phase(), 'retry_wait')
        self.assertEqual(self.phase(expired=True), 'retry_pending')

    def test_only_current_completed_third_failure_is_exhausted(self):
        self.assertEqual(self.phase(count=3), 'exhausted')
        self.assertEqual(self.phase(count=3, status='running'), 'running')
        self.assertEqual(self.phase(count=3, processor='old'), 'retry_pending')

    def test_successful_retry_is_read_from_current_document(self):
        self.assertEqual(self.phase(count=2, status='ready', ready=True), 'ready')
