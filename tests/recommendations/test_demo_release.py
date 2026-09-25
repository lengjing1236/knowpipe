import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mongomock

from scripts.demo_release import chinese_corpus, prepare_database, worker_environment, stop_services, status


class DemoReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.corpus = self.root / 'documents.jsonl'
        self.records = [dict(source='official', doc_id=str(i), title='中文测试资料',
                             body_text='异常捕获和日志记录的原始完整正文。',
                             language=language, source_url='https://example.com/docs',
                             license='test fixture', fulltext_verified=True)
                        for i, language in enumerate(('zh', 'en', 'zh-CN'))]
        self.write_records()
        self.db = mongomock.MongoClient().demo

    def write_records(self):
        self.corpus.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in self.records))

    def test_all_chinese_input_selected_and_provenance_validated(self):
        records, summary = chinese_corpus(self.corpus)
        self.assertEqual([r['doc_id'] for r in records], ['0', '2'])
        self.assertEqual(summary['input_document_count'], 3)
        self.records[0]['fulltext_verified'] = False
        self.write_records()
        with self.assertRaisesRegex(ValueError, 'fulltext_verification'):
            chinese_corpus(self.corpus)

    def test_existing_database_is_never_modified(self):
        self.db.documents.insert_one({'source': 'existing', 'doc_id': 'untouched'})
        with self.assertRaisesRegex(ValueError, '数据库已存在'):
            prepare_database(self.db, self.root / 'state', self.corpus, '学习 Python 异常处理')
        self.assertEqual(self.db.documents.count_documents({}), 1)
        self.assertEqual(self.db.list_collection_names(), ['documents'])

    def test_prepare_retains_actual_history_and_does_not_inject_results(self):
        state = self.root / 'state'
        first = prepare_database(self.db, state, self.corpus, '学习 Python 异常处理')
        account = json.loads((state / 'account.json').read_text())
        self.assertEqual((state / 'account.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(first['ready_job_injection'])
        self.assertEqual(self.db.recommendation_jobs.count_documents({}), 0)
        self.db.user_profiles.update_one({'user_id': account['user_id']}, {'$set': {'read_doc_ids': ['official:0']}})
        prepare_database(self.db, state, self.corpus, '不覆盖已保存目标')
        profile = self.db.user_profiles.find_one({'user_id': account['user_id']})
        self.assertEqual(profile['read_doc_ids'], ['official:0'])
        self.assertEqual(profile['learning_goal']['text'], '学习 Python 异常处理')
        self.assertEqual(self.db.documents.count_documents({}), 2)
        self.records[0]['body_text'] += '改动'
        self.write_records()
        with self.assertRaisesRegex(ValueError, '输入语料已变化'):
            prepare_database(self.db, state, self.corpus, '学习 Python 异常处理')

    def test_remote_only_and_private_file_key_without_log_persistence(self):
        key_file = self.root / 'key'
        key_file.write_text('private-test-value\n')
        env = worker_environment({'KNOWPIPE_NLLB_MODEL_PATH': '/unused/local',
                                  'KNOWPIPE_LLAMA_TRANSLATION_URL': 'http://unused',
                                  'KNOWPIPE_TRANSLATION_PROVIDER': 'local'},
                                 state=self.root, mongo_uri='mongodb://localhost', database='demo',
                                 semantic_root=self.root, api_key_file=key_file)
        self.assertNotIn('KNOWPIPE_NLLB_MODEL_PATH', env)
        self.assertNotIn('KNOWPIPE_LLAMA_TRANSLATION_URL', env)
        self.assertEqual(env['KNOWPIPE_TRANSLATION_PROVIDER'], 'bigmodel-free')
        self.assertEqual(env['KNOWPIPE_BIGMODEL_API_KEY'], 'private-test-value')
        self.assertEqual(env['SPARK_MASTER'], 'local[1]')
        self.assertNotIn('private-test-value', (self.root / 'secret-key').read_text())

    def test_stop_only_interrupts_verified_recommendation_worker(self):
        import signal
        manifest = {'processes': [{'name': 'web', 'pid': 1},
                                  {'name': 'recommendations', 'pid': 2}]}
        with patch('scripts.demo_release.running', side_effect=[True, False]), \
             patch('scripts.demo_release.os.kill') as kill, \
             patch('scripts.demo_release.stop_processes') as stop:
            stop_services(manifest)
        kill.assert_called_once_with(2, signal.SIGINT)
        stop.assert_called_once_with(manifest)

    def test_status_cannot_create_profile_in_a_mismatched_database(self):
        state = self.root / 'state'
        prepare_database(self.db, state, self.corpus, '学习 Python 异常处理')
        unrelated = self.db.client.unrelated
        with self.assertRaisesRegex(ValueError, '另一个数据库'):
            status(state, unrelated)
        self.assertEqual(unrelated.list_collection_names(), [])
