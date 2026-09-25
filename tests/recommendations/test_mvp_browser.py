"""Acceptance decisions only: no browser, Mongo server, Spark or model is started."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from scripts import acceptance_mvp011 as acceptance
from knowpipe.learning.content import content_version
from knowpipe.learning.quality import CHECKS_VERSION


class BrowserTranslationWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
        self.doc = {'source': 'docs', 'doc_id': 'one', 'body_text': 'Complete source material.', 'language': 'en',
                    'translation_expected_processor': 'local-model-v1'}
        self.version = content_version(self.doc)
        self.item = {'source': 'docs', 'doc_id': 'one', 'content_version': self.version}
        self.doc.update(content={'kind': 'fulltext', 'version': self.version},
                        processing={'translation': {'status': 'failed', 'source_version': self.version,
                                                    'error_code': 'translation_quality_failed'}},
                        translation_attempts={'count': 1, 'processor_id': 'local-model-v1',
                                              'source_version': self.version,
                                              'retry_at': self.now + timedelta(minutes=2)},
                        translation_quality={'source_version': self.version, 'processor_id': 'local-model-v1',
                                             'checks_version': CHECKS_VERSION, 'status': 'failed',
                                             'issues': ['identifier_missing']})

    def observe(self, **changes):
        doc = deepcopy(self.doc)
        doc.update(changes)
        return acceptance.translation_observation(self.item, doc, now=self.now)

    def test_first_failed_attempt_waits_and_records_its_identity_and_quality(self):
        observed = self.observe()
        self.assertEqual(observed['phase'], 'retry_wait')
        self.assertEqual(acceptance.translation_wait_decision([observed]), 'waiting')
        self.assertEqual(observed['attempts']['count'], 1)
        self.assertEqual(observed['attempts']['source_version'], self.version)
        self.assertEqual(observed['attempts']['retry_at'], '2026-09-25T14:02:00+00:00')
        self.assertEqual(observed['quality']['issues'], ['identifier_missing'])

    def test_past_retry_time_is_not_exhaustion_and_mongo_naive_utc_is_supported(self):
        self.doc['translation_attempts']['retry_at'] = self.now.replace(tzinfo=None) - timedelta(seconds=1)
        observed = self.observe()
        self.assertEqual(observed['phase'], 'retry_pending')
        self.assertEqual(acceptance.translation_wait_decision([observed]), 'waiting')

    def test_third_running_attempt_may_still_succeed_and_timeout_is_explicit(self):
        self.doc['translation_attempts']['count'] = 3
        self.doc['processing']['translation']['status'] = 'running'
        observed = self.observe()
        self.assertEqual(observed['phase'], 'running')
        self.assertEqual(acceptance.translation_wait_decision([observed]), 'waiting')
        self.assertEqual(acceptance.translation_wait_decision([observed], timed_out=True), 'timeout_running')

    def test_terminal_requires_all_foreign_candidates_exhausted_or_unavailable(self):
        retry = self.observe()
        self.doc['translation_attempts']['count'] = 3
        exhausted = self.observe()
        self.doc['processing']['translation']['status'] = 'unavailable'
        unavailable = self.observe()
        self.assertEqual(exhausted['phase'], 'exhausted')
        self.assertEqual(unavailable['phase'], 'unavailable')
        terminal = [exhausted, unavailable]
        self.assertEqual(acceptance.translation_wait_decision(terminal), 'confirming_terminal_failure')
        self.assertEqual(acceptance.translation_wait_decision(terminal, previous_documents=terminal), 'terminal_failure')
        self.assertEqual(acceptance.translation_wait_decision([exhausted, unavailable, retry]), 'waiting')

    def test_terminal_observation_is_rechecked_before_abort(self):
        self.doc['translation_attempts']['count'] = 3
        report = {}
        terminal = [self.observe()]
        self.assertEqual(acceptance.record_translation_observation(report, terminal), 'confirming_terminal_failure')
        self.doc['processing']['translation']['status'] = 'running'
        self.assertEqual(acceptance.record_translation_observation(report, [self.observe()]), 'waiting')
        self.assertEqual(acceptance.record_translation_observation(report, terminal), 'confirming_terminal_failure')
        self.assertEqual(acceptance.record_translation_observation(report, terminal), 'terminal_failure')

    def test_old_version_or_processor_attempts_cannot_exhaust_current_translation(self):
        self.doc['translation_attempts']['count'] = 3
        for field, old_value in [('source_version', 'old-version'), ('processor_id', 'old-model')]:
            attempts = {**self.doc['translation_attempts'], field: old_value}
            observed = self.observe(translation_attempts=attempts)
            self.assertFalse(observed['attempts']['matches_current'])
            self.assertEqual(acceptance.translation_wait_decision([observed]), 'waiting')

    def test_chinese_original_does_not_substitute_for_actual_english_translation(self):
        original = self.observe(language='zh')
        self.assertEqual(acceptance.translation_wait_decision([original]), 'no_translation_candidate')
        self.doc['translation'] = {'source_version': self.version, 'complete': True, 'language': 'zh',
                                   'text': '完整原文材料。', 'processor_id': 'local-model-v1',
                                   'quality': {'checks_version': CHECKS_VERSION, 'status': 'checks_passed', 'issues': []}}
        observed = self.observe()
        self.assertEqual(acceptance.translation_wait_decision([observed]), 'ready')
        stale_item = {**self.item, 'content_version': 'old-version'}
        stale = acceptance.translation_observation(stale_item, self.doc, now=self.now)
        self.assertFalse(stale['chinese_ready'])
        self.assertEqual(acceptance.translation_wait_decision([stale]), 'waiting')

    def test_report_retains_failure_before_retry_and_records_timeout(self):
        report = {}
        first = self.observe()
        acceptance.record_translation_observation(report, [first])
        acceptance.record_translation_observation(report, [first])
        self.doc['processing']['translation']['status'] = 'running'
        self.doc.pop('translation_quality')
        running = self.observe()
        acceptance.record_translation_observation(report, [running], timed_out=True)
        self.assertEqual(len(report['translation_observations']), 2)
        self.assertEqual(report['translation_observations'][0]['documents'][0]['quality']['issues'], ['identifier_missing'])
        self.assertEqual(report['translation_wait_outcome'], 'timeout_running')
        self.assertEqual(acceptance.translation_wait_decision([first], timed_out=True), 'timeout_pending')


class BrowserFailureArtifactsTests(unittest.TestCase):
    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            acceptance.require_fresh_output(out)
            (out / 'browser.json').write_text('existing failure')
            with self.assertRaises(FileExistsError):
                acceptance.require_fresh_output(out)
            self.assertEqual((out / 'browser.json').read_text(), 'existing failure')

    def test_failure_is_captured_before_browser_closes(self):
        manager = MagicMock()
        browser = manager.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        page.is_closed.return_value = False
        operations = []
        page.screenshot.side_effect = lambda **_: operations.append('capture')
        browser.close.side_effect = lambda: operations.append('close')
        report = {}
        with patch.object(acceptance, 'sync_playwright', return_value=manager):
            with self.assertRaisesRegex(TimeoutError, 'translation_timeout_running'):
                with acceptance.acceptance_page({}, Path('/unused'), report):
                    raise TimeoutError('translation_timeout_running')
        self.assertEqual(operations, ['capture', 'close'])
        self.assertEqual(report['failure_screenshot']['status'], 'saved')

    def test_screenshot_failure_does_not_replace_the_original_failure(self):
        page = Mock()
        page.is_closed.return_value = False
        page.screenshot.side_effect = RuntimeError('browser unavailable')
        report = {}
        acceptance.capture_failure_page(page, Path('/unused'), report)
        self.assertEqual(report['failure_screenshot']['error_type'], 'RuntimeError')
        page.is_closed.return_value = True
        acceptance.capture_failure_page(page, Path('/unused'), report)
        self.assertEqual(report['failure_screenshot']['reason'], 'page_not_open')


if __name__ == '__main__':
    unittest.main()
