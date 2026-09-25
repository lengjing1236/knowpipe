import unittest
from unittest.mock import Mock
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
