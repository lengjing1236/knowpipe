import unittest
from datetime import timedelta

import mongomock

from knowpipe.learning.store import save_goal
from knowpipe.learning.content import publish_fulltext, content_version
from knowpipe.recommendations import queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        queue.ensure_indexes(self.db)
        self.now = queue.utcnow()
        self.owner = queue.acquire_worker(self.db, now=self.now)
        self.snapshot = {'corpus_id': 'corpus-a', 'document_count': 1, 'paragraph_count': 1, 'sources': {'docs': 1}}
        self.runtime = queue.publish_corpus(self.db, self.owner, self.snapshot, now=self.now)
        save_goal(self.db, 'alice', 'Redis 持久化')

    def schedule(self):
        profile = self.db.user_profiles.find_one({'user_id': 'alice'})
        return queue.schedule(self.db, profile, self.runtime, now=self.now)

    def test_deduplication_and_goal_revision(self):
        first = self.schedule()
        self.assertEqual(first, self.schedule())
        self.assertEqual(self.db.recommendation_jobs.count_documents({}), 1)
        save_goal(self.db, 'alice', '数据库事务')
        self.assertNotEqual(first, self.schedule())
        self.assertEqual(queue.view(self.db, 'bob')['status'], 'needs_goal')

    def test_processor_upgrade_invalidates_result_without_feature_rebuild(self):
        old_id = self.schedule()
        old_job = self.db.recommendation_jobs.find_one({'_id': old_id})
        prior_generation = self.runtime['generation']
        self.runtime = queue.publish_corpus(self.db, self.owner,
            {**self.snapshot, 'processing_id': 'new-model-and-rules'}, now=self.now)
        self.assertEqual(self.runtime['generation'], prior_generation + 1)
        self.assertFalse(queue.is_current(self.db, old_job, self.snapshot['corpus_id']))
        self.assertEqual(queue.view(self.db, 'alice')['reason'], 'processing_changed')
        self.assertNotEqual(old_id, self.schedule())
        self.assertEqual(self.runtime['corpus']['corpus_id'], self.snapshot['corpus_id'])

    def test_explicit_refresh_recovers_degraded_query_without_changing_goal(self):
        job_id = self.schedule()
        self.db.documents.insert_one({'source': 'docs', 'doc_id': 'selected', 'content': {'version': 'v'},
            'processing': {'translation': {'status': 'failed'}}, 'translation_attempts': {'count': 3}})
        self.db.recommendation_jobs.update_one({'_id': job_id}, {'$set': {'status': 'empty', 'attempts': 1,
            'result': {'items': [{'source': 'docs', 'doc_id': 'selected', 'content_version': 'v'}], 'query': {'status': 'failed'}}}})
        queue.request_refresh(self.db, 'alice')
        job = self.db.recommendation_jobs.find_one({'_id': job_id})
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['attempts'], 0)
        self.assertNotIn('result', job)
        self.assertNotIn('translation_attempts', self.db.documents.find_one({'doc_id': 'selected'}))

    def test_worker_and_job_expiry_fence_old_attempts(self):
        self.assertIsNone(queue.acquire_worker(self.db, now=self.now))
        job_id = self.schedule()
        first = queue.claim(self.db, self.owner, now=self.now)
        self.assertIsNone(queue.claim(self.db, self.owner, now=self.now))
        later = self.now + timedelta(seconds=queue.LEASE_SECONDS + 1)
        new_owner = queue.acquire_worker(self.db, now=later)
        self.assertIsNone(queue.publish_corpus(self.db, self.owner, self.snapshot, now=later))
        second = queue.claim(self.db, new_owner, now=later)
        self.assertEqual(job_id, second['_id'])
        self.assertNotEqual(first['attempt_token'], second['attempt_token'])
        self.assertFalse(queue.finish(self.db, first, {'items': []}, now=later))
        self.assertTrue(queue.finish(self.db, second, {'items': []}, now=later))
        self.assertEqual(queue.view(self.db, 'alice')['status'], 'empty')

    def test_job_lease_never_outlives_worker_lease(self):
        self.schedule()
        shortly = self.now + timedelta(seconds=5)
        self.db.recommendation_runtime.update_one({'_id': 'worker'}, {'$set': {'lease_expires_at': shortly}})
        job = queue.claim(self.db, self.owner, now=self.now)
        self.assertEqual(job['lease_expires_at'], shortly.replace(microsecond=shortly.microsecond // 1000 * 1000))
        self.assertFalse(queue.finish(self.db, job, {'items': []}, now=shortly + timedelta(seconds=1)))

    def test_bounded_retries_and_explicit_retry(self):
        job_id = self.schedule()
        for _ in range(3):
            job = queue.claim(self.db, self.owner, now=self.now)
            self.assertIsNotNone(job)
            queue.fail(self.db, job, 'computation_failed', now=self.now)
        self.assertIsNone(queue.claim(self.db, self.owner, now=self.now))
        self.assertEqual(self.db.recommendation_jobs.find_one({'_id': job_id})['status'], 'failed')
        queue.request_refresh(self.db, 'alice')
        self.assertIsNotNone(queue.claim(self.db, self.owner, now=self.now))

    def test_corpus_limit_failure_is_visible_even_with_previous_result(self):
        self.schedule()
        job = queue.claim(self.db, self.owner, now=self.now)
        queue.finish(self.db, job, {'items': []}, now=self.now)
        self.db.recommendation_runtime.update_one({'_id': 'worker'}, {'$set': {'error_code': 'document_limit_exceeded'}})
        result = queue.view(self.db, 'alice')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'document_limit_exceeded')
        self.assertEqual(result['items'], [])

    def test_history_and_candidate_version_changes_hide_result(self):
        for doc_id in ('candidate', 'history'):
            self.db.documents.insert_one({'source': 'docs', 'doc_id': doc_id})
            publish_fulltext(self.db, 'docs', doc_id, 'Redis 持久化保存数据', 'zh')
        refs = [{'source': 'docs', 'doc_id': doc_id, 'content_version': content_version(
            self.db.documents.find_one({'doc_id': doc_id}))} for doc_id in ('candidate', 'history')]
        self.schedule()
        job = queue.claim(self.db, self.owner, now=self.now)
        queue.finish(self.db, job, {'items': [refs[0]], 'history_references': [refs[1]]}, now=self.now)
        self.assertEqual(queue.view(self.db, 'alice')['status'], 'ready')
        publish_fulltext(self.db, 'docs', 'history', '正文已更新，旧对照不能继续展示', 'zh')
        view = queue.view(self.db, 'alice')
        self.assertEqual(view['status'], 'stale')
        self.assertEqual(view['items'], [])

    def test_old_profile_or_corpus_cannot_replace_new_pointer(self):
        old_profile = self.db.user_profiles.find_one({'user_id': 'alice'})
        first = self.schedule()
        new_runtime = queue.publish_corpus(self.db, self.owner, dict(self.snapshot, corpus_id='corpus-b'), now=self.now)
        second = queue.schedule(self.db, old_profile, new_runtime, now=self.now)
        self.assertNotEqual(first, second)
        queue.schedule(self.db, old_profile, self.runtime, now=self.now)
        self.assertEqual(self.db.user_profiles.find_one({'user_id': 'alice'})['desired_recommendation_job'], second)
        save_goal(self.db, 'alice', '新目标')
        self.assertIsNone(queue.schedule(self.db, old_profile, new_runtime, now=self.now))
        self.assertEqual(queue.view(self.db, 'alice')['items'], [])

    def test_returning_to_prior_corpus_revives_stale_job(self):
        initial=self.schedule()
        self.db.recommendation_jobs.update_one({'_id':initial},{'$set':{'status':'stale','attempts':1}})
        runtime_b=queue.publish_corpus(self.db,self.owner,dict(self.snapshot,corpus_id='other'),now=self.now)
        queue.schedule(self.db,self.db.user_profiles.find_one({'user_id':'alice'}),runtime_b,now=self.now)
        runtime_a=queue.publish_corpus(self.db,self.owner,self.snapshot,now=self.now)
        revived=queue.schedule(self.db,self.db.user_profiles.find_one({'user_id':'alice'}),runtime_a,now=self.now)
        self.assertEqual(revived,initial)
        self.assertEqual(self.db.recommendation_jobs.find_one({'_id':initial})['status'],'queued')

    def test_same_corpus_new_feature_basis_invalidates_prior_results(self):
        runtime_a=queue.publish_corpus(self.db,self.owner,dict(self.snapshot,feature_id='basis-a'),now=self.now)
        profile=self.db.user_profiles.find_one({'user_id':'alice'})
        first=queue.schedule(self.db,profile,runtime_a,now=self.now)
        old_job=queue.claim(self.db,self.owner,now=self.now)
        queue.finish(self.db,old_job,{'items':[]},now=self.now)
        runtime_b=queue.publish_corpus(self.db,self.owner,dict(self.snapshot,feature_id='basis-b'),now=self.now)
        self.assertEqual(queue.view(self.db,'alice')['status'],'stale')
        second=queue.schedule(self.db,profile,runtime_b,now=self.now)
        self.assertNotEqual(first,second)
        self.assertFalse(queue.is_current(self.db,old_job,self.snapshot['corpus_id']))
