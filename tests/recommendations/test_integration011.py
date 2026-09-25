"""Semantic provenance must control tasks, retries and notifications."""
from unittest.mock import Mock
import mongomock
from knowpipe.learning.providers import UnconfiguredProvider
from knowpipe.learning.store import save_goal
from knowpipe.recommendations import queue
from knowpipe.recommendations.query import processing_identity
from tests.podcasts.test_learning_pipeline import setup_notice
from knowpipe.podcasts.learning import publish_recommendations


def test_semantic_model_identity_invalidates_pipeline():
    language = UnconfiguredProvider()
    old = processing_identity(language, language, Mock(processor_id='semantic-v1'))
    assert old != processing_identity(language, language, Mock(processor_id='semantic-v2'))
    assert old != processing_identity(language, language)


def test_lexical_fallback_cannot_send_podcast_supplement():
    db, job, item, _ = setup_notice()
    item['supplement_eligible'] = True
    job['history'] = [{'source': 'docs', 'doc_id': 'read'}]
    result = {'items': [item], 'history_relevant_paragraphs': 1,
              'semantic': {'status': 'unavailable'}}
    assert publish_recommendations(db, job, result) == 0
    assert db.notifications.count_documents({}) == 0


def test_failed_semantic_retries_on_explicit_refresh():
    db = mongomock.MongoClient().db
    queue.ensure_indexes(db)
    save_goal(db, 'u', 'PostgreSQL transaction recovery')
    owner = queue.acquire_worker(db)
    runtime = queue.publish_corpus(db, owner, {'corpus_id': 'c', 'feature_id': 'f', 'processing_id': 'p'})
    job_id = queue.schedule(db, db.user_profiles.find_one({'user_id': 'u'}), runtime)
    db.recommendation_jobs.update_one({'_id': job_id}, {'$set': {'status': 'empty', 'result': {
        'items': [], 'query': {'status': 'native'}, 'semantic': {'status': 'failed'}}}})
    queue.request_refresh(db, 'u')
    assert db.recommendation_jobs.find_one({'_id': job_id})['status'] == 'queued'


def test_real_result_published_before_slow_translation_without_expiring_job_lease(monkeypatch):
    import time
    from types import SimpleNamespace
    from knowpipe.recommendations.worker import RecommendationWorker
    db = mongomock.MongoClient().db
    worker = RecommendationWorker(db, '/unused', spark=object())
    save_goal(db, 'u', 'PostgreSQL transaction recovery')
    worker.owner = queue.acquire_worker(db)
    snapshot = {'corpus_id': 'c', 'feature_id': 'f', 'processing_id': 'p'}
    queue.publish_corpus(db, worker.owner, snapshot)
    worker.index = SimpleNamespace(snapshot=snapshot)
    worker.last_refresh = time.monotonic()
    monkeypatch.setattr('knowpipe.recommendations.worker.recommend', lambda *a, **kw: {'items': []})
    def translate(result, *, guard, generation):
        assert db.recommendation_jobs.find_one({})['status'] == 'empty'
        assert guard()  # finished job is no longer a running lease, worker guard still owns it
    monkeypatch.setattr(worker, 'prepare_chinese', translate)
    assert worker.run_once()


def test_notification_requires_actual_comparison_state_and_deduplicates():
    db, job, item, _ = setup_notice()
    job['history'] = [{'source': 'docs', 'doc_id': 'read'}]
    item['supplement_eligible'] = True
    result = {'items': [item], 'history_relevant_paragraphs': 1, 'semantic': {'status': 'ready', 'processor_id': 'semantic-real-version'}}
    for status in ('covered', 'uncertain', 'goal_only'):
        item['comparison'] = {'status': status}
        assert publish_recommendations(db, job, result) == 0
    item['comparison']['status'] = 'possible_supplement'
    assert publish_recommendations(db, job, result) == 1
    assert publish_recommendations(db, job, result) == 0
    notice = db.notifications.find_one({})
    assert notice['recommendation_mode'] == 'supplement'
    assert notice['semantic_processor_id'] == 'semantic-real-version'


def test_official_object_identity_uses_adapter_and_exact_origin_not_keyword():
    from knowpipe.recommendations.query import document_matches_entity, entity_evidence
    python = {'name': 'Python', 'aliases': ['python']}
    url = 'https://docs.python.org/zh-cn/3/library/asyncio-sync.html'
    assert document_matches_entity('同步原语', '信号量控制并发访问数量。', 'python_docs', url, python)
    evidence = entity_evidence('同步原语', '信号量控制并发访问数量。', [python], 'python_docs', url)
    assert evidence[0]['field'] == 'source_url' and evidence[0]['text'] == url
    for source, fake in [('stackexchange', url), ('python_docs', 'https://docs.python.org.evil.test/library'),
                         ('python_docs', 'https://docs.python.org@evil.test/library')]:
        assert not document_matches_entity('', 'Semaphore reference.', source, fake, python)
    assert not document_matches_entity('', '', 'python_docs', url, {'name': 'Django', 'aliases': ['django']})
