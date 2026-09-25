import unittest
from unittest.mock import Mock, patch
import mongomock
from knowpipe.learning.content import content_view, publish_fulltext, content_version
from knowpipe.learning.providers import TextResult
from knowpipe.recommendations.worker import RecommendationWorker


class WorkerTests(unittest.TestCase):
    def test_lost_ownership_stops_before_next_document(self):
        db = mongomock.MongoClient().db
        items = []
        for doc_id in ('first', 'second'):
            db.documents.insert_one({'source': 'docs', 'doc_id': doc_id})
            doc = publish_fulltext(db, 'docs', doc_id, 'Complete public document.', 'en')
            items.append({'source': 'docs', 'doc_id': doc_id, 'content_version': content_version(doc)})
        provider = Mock(processor_id='current')
        provider.translate.return_value = TextResult('完整公共资料。', 'zh')
        guard = Mock(side_effect=[True, True, False])
        worker = RecommendationWorker(db, '/unused', translator=provider)
        worker.prepare_chinese({'items': items}, guard=guard, generation=2)
        provider.translate.assert_called_once()
        self.assertFalse(content_view(db.documents.find_one({'doc_id': 'second'}))['chinese_ready'])

    def test_interrupted_exhausted_translation_in_previous_corpus_generation(self):
        from knowpipe.learning.providers import processor_identity
        db = mongomock.MongoClient().db
        db.documents.insert_one({'source': 'docs', 'doc_id': 'exhausted'})
        doc = publish_fulltext(db, 'docs', 'exhausted', 'Complete source.', 'en')
        provider = Mock(processor_id='stable-model')
        version = content_version(doc)
        db.documents.update_one({'doc_id': 'exhausted'}, {'$set': {
            'translation_generation': 1, 'translation_expected_processor': processor_identity(provider),
            'processing.translation': {'status': 'running', 'source_version': version},
            'translation_attempts': {'source_version': version, 'processor_id': processor_identity(provider), 'count': 3}}})
        worker = RecommendationWorker(db, '/unused', translator=provider)
        worker.prepare_chinese({'items': [{'source': 'docs', 'doc_id': 'exhausted', 'content_version': version}]}, generation=2)
        view = content_view(db.documents.find_one({'doc_id': 'exhausted'}))
        self.assertEqual(view['processing']['translation']['status'], 'failed')
        provider.translate.assert_not_called()

    def test_translation_only_for_selected_current_versions_and_cached(self):
        db = mongomock.MongoClient().db
        for doc_id in ('selected', 'not-selected', 'changed'):
            db.documents.insert_one({'source': 'docs', 'doc_id': doc_id})
            publish_fulltext(db, 'docs', doc_id, 'Redis persistence uses append only logging.', 'en')
        result = {'items': [{'source': 'docs', 'doc_id': 'selected', 'content_version': content_version(
            db.documents.find_one({'doc_id': 'selected'}))}, {'source': 'docs', 'doc_id': 'changed', 'content_version': 'old'}]}
        provider = Mock()
        provider.translate.return_value = TextResult('这是测试提供方返回的完整中文正文。', 'zh')
        worker = RecommendationWorker(db, '/unused', translator=provider)
        worker.prepare_chinese(result)
        worker.prepare_chinese(result)
        provider.translate.assert_called_once()
        self.assertTrue(content_view(db.documents.find_one({'doc_id': 'selected'}))['chinese_ready'])
        self.assertFalse(content_view(db.documents.find_one({'doc_id': 'not-selected'}))['chinese_ready'])

    def test_unconfigured_provider_does_not_claim_translation_success(self):
        db = mongomock.MongoClient().db
        db.documents.insert_one({'source': 'docs', 'doc_id': '1'})
        doc = publish_fulltext(db, 'docs', '1', 'Redis persistence uses append only logging.', 'en')
        worker = RecommendationWorker(db, '/unused')
        worker.prepare_chinese({'items': [{'source': 'docs', 'doc_id': '1', 'content_version': content_version(doc)}]})
        view = content_view(db.documents.find_one({}))
        self.assertFalse(view['chinese_ready'])
        self.assertEqual(view['processing']['translation']['status'], 'unavailable')

    def test_failed_translation_has_bounded_automatic_retry_and_explicit_recovery(self):
        from datetime import timedelta
        from knowpipe.recommendations import queue
        from knowpipe.learning.store import save_goal
        db = mongomock.MongoClient().db
        db.documents.insert_one({'source': 'docs', 'doc_id': 'retry'})
        doc = publish_fulltext(db, 'docs', 'retry', 'Database transaction recovery.', 'en')
        item = {'source': 'docs', 'doc_id': 'retry', 'content_version': content_version(doc)}
        provider = Mock()
        provider.translate.side_effect = RuntimeError('failure')
        worker = RecommendationWorker(db, '/unused', translator=provider)
        for _ in range(3):
            db.documents.update_one({'doc_id': 'retry'}, {'$set': {'translation_attempts.retry_at': queue.utcnow() - timedelta(seconds=1)}})
            worker.prepare_chinese({'items': [item]})
        worker.prepare_chinese({'items': [item]})
        self.assertEqual(provider.translate.call_count, 3)
        db.documents.update_one({'doc_id': 'retry'}, {'$set': {'processing.translation.status': 'running'}})
        worker.prepare_chinese({'items': [item]})
        self.assertEqual(content_view(db.documents.find_one({'doc_id': 'retry'}))['processing']['translation']['status'], 'failed')
        db.documents.update_one({'doc_id': 'retry'}, {'$set': {'processing.translation.status': 'running'}})
        save_goal(db, 'u', 'database transaction')
        profile = db.user_profiles.find_one({'user_id': 'u'})
        db.user_profiles.update_one({'user_id': 'u'}, {'$set': {'desired_recommendation_job': 'retry-job'}})
        db.recommendation_jobs.insert_one({'_id': 'retry-job', 'user_id': 'u', 'revision': profile['learning_revision'],
                                           'status': 'ready', 'corpus_id': 'c', 'result': {'items': [item]}})
        db.recommendation_runtime.update_one({'_id': 'worker'}, {'$set': {'corpus': {'corpus_id': 'c'}}})
        queue.request_refresh(db, 'u')
        provider.translate.side_effect = None
        provider.translate.return_value = TextResult('数据库事务恢复。', 'zh')
        worker.prepare_chinese({'items': [item]})
        self.assertTrue(content_view(db.documents.find_one({'doc_id': 'retry'}))['chinese_ready'])


class SemanticLifecycleWorkerTests(unittest.TestCase):
    def worker(self, semantic):
        import time
        from types import SimpleNamespace
        from knowpipe.learning.providers import UnconfiguredProvider
        from knowpipe.learning.store import save_goal
        from knowpipe.recommendations import queue
        db = mongomock.MongoClient().db
        worker = RecommendationWorker(db, '/unused', spark=object(), semantic=semantic,
                                      translator=UnconfiguredProvider(), goal_translator=UnconfiguredProvider())
        save_goal(db, 'u', 'PostgreSQL transaction recovery')
        worker.owner = queue.acquire_worker(db)
        snapshot = {'corpus_id': 'c', 'feature_id': 'f', 'processing_id': 'p'}
        queue.publish_corpus(db, worker.owner, snapshot)
        worker.index = SimpleNamespace(snapshot=snapshot)
        worker.last_refresh = time.monotonic()
        return worker, db

    def result(self):
        return {'items': [{'source': 'docs', 'doc_id': 'candidate', 'content_version': 'v'}],
                'semantic': {'status': 'ready', 'processor_id': 'semantic-test'}}

    def test_releases_after_publication_before_translation_and_reuses_same_provider(self):
        from knowpipe.learning.store import save_goal
        events, provider_ids = [], []

        class ReloadableSemantic:
            processor_id = 'semantic-test'

            def __init__(self):
                self.session = None
                self.loads = 0

            def relevance(self):
                if self.session is None:
                    self.session = object()
                    self.loads += 1
                events.append('compute')
                return .75

            def close(self):
                # The job has been published before resource release.
                self_test.assertEqual(db.recommendation_jobs.find_one(
                    {'_id': db.user_profiles.find_one({'user_id': 'u'})['desired_recommendation_job']})['status'], 'ready')
                self.session = None
                events.append('release')

        self_test = self
        semantic = ReloadableSemantic()
        worker, db = self.worker(semantic)
        expected = self.result()

        def compute(*args, **kwargs):
            self.assertIs(kwargs['semantic'], semantic)
            provider_ids.append(id(kwargs['semantic']))
            score = semantic.relevance()
            return {**expected, 'score': score}

        def translate(result, *, guard, generation):
            self.assertIsNone(semantic.session)
            self.assertEqual(result, {**expected, 'score': .75})
            job = db.recommendation_jobs.find_one(
                {'_id': db.user_profiles.find_one({'user_id': 'u'})['desired_recommendation_job']})
            self.assertEqual(job['result'], result)
            self.assertTrue(guard())
            events.append('translate')

        with patch('knowpipe.recommendations.worker.recommend', side_effect=compute), \
                patch.object(worker, 'prepare_chinese', side_effect=translate), \
                patch('knowpipe.recommendations.worker.publish_recommendations', side_effect=lambda *_: events.append('notify')):
            self.assertTrue(worker.run_once())
            save_goal(db, 'u', 'PostgreSQL transaction retry')
            self.assertTrue(worker.run_once())
        self.assertEqual(events, ['compute', 'release', 'translate', 'notify'] * 2)
        self.assertEqual(semantic.loads, 2)
        self.assertEqual(provider_ids, [id(semantic), id(semantic)])
        self.assertEqual(semantic.processor_id, 'semantic-test')

    def test_computation_failure_still_releases_and_preserves_original_error(self):
        semantic = Mock(processor_id='semantic-test')
        worker, db = self.worker(semantic)
        with patch('knowpipe.recommendations.worker.recommend', side_effect=ValueError('history_limit_exceeded')), \
                patch.object(worker, 'prepare_chinese') as translate, \
                self.assertLogs('knowpipe.recommendations.worker', level='ERROR'):
            self.assertTrue(worker.run_once())
        semantic.close.assert_called_once_with()
        translate.assert_not_called()
        self.assertEqual(db.recommendation_jobs.find_one({})['error_code'], 'history_limit_exceeded')

    def test_provider_without_close_is_compatible(self):
        from types import SimpleNamespace
        for semantic in (SimpleNamespace(processor_id='semantic-test'),
                         SimpleNamespace(processor_id='semantic-test', close=None)):
            with self.subTest(provider=semantic):
                worker, db = self.worker(semantic)
                with patch('knowpipe.recommendations.worker.recommend', return_value=self.result()), \
                        patch.object(worker, 'prepare_chinese') as translate, \
                        patch('knowpipe.recommendations.worker.publish_recommendations'):
                    self.assertTrue(worker.run_once())
                translate.assert_called_once()
                self.assertEqual(db.recommendation_jobs.find_one({})['status'], 'ready')

    def test_cleanup_failure_cannot_replace_result_or_original_computation_error(self):
        for compute_error in (None, ValueError('history_limit_exceeded')):
            with self.subTest(compute_error=compute_error):
                semantic = Mock(processor_id='semantic-test')
                semantic.close.side_effect = RuntimeError('private provider detail')
                worker, db = self.worker(semantic)
                with patch('knowpipe.recommendations.worker.recommend', return_value=self.result(), side_effect=compute_error), \
                        patch.object(worker, 'prepare_chinese') as translate, \
                        patch('knowpipe.recommendations.worker.publish_recommendations'), \
                        self.assertLogs('knowpipe.recommendations.worker', level='WARNING') as logs:
                    self.assertTrue(worker.run_once())
                semantic.close.assert_called_once_with()
                output = '\n'.join(logs.output)
                self.assertIn('recommendation_semantic_release_failed: RuntimeError', output)
                self.assertNotIn('private provider detail', output)
                job = db.recommendation_jobs.find_one({})
                if compute_error is None:
                    self.assertEqual(job['status'], 'ready')
                    self.assertEqual(job['result'], self.result())
                    translate.assert_called_once()
                else:
                    self.assertEqual(job['error_code'], 'history_limit_exceeded')
                    translate.assert_not_called()
