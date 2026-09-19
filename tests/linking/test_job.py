import unittest
from unittest.mock import patch
import mongomock
from knowpipe.linking.job import rank_links, run, for_episode, document_fingerprint, episode_fingerprint
from knowpipe.web.app import create_app


class LinkingSparkTests(unittest.TestCase):
    def test_shared_space_ranks_related_and_keeps_source_identity(self):
        from pyspark.sql import SparkSession
        spark=(SparkSession.builder.master('local[2]').appName('link-test')
               .config('spark.sql.shuffle.partitions','2').getOrCreate())
        docs=[{'source':'stackexchange','doc_id':'1','title':'Spark','body_text':'Spark distributed data partition processing'},
              {'source':'arxiv','doc_id':'1','title':'Spark','body_text':'Spark distributed data partition processing'},
              {'source':'arxiv','doc_id':'2','title':'Gardening','body_text':'flowers garden water roses'}]
        episodes=[{'episode_id':'e','batch_id':'b','analysis':{'segments':[
            {'segment_id':0,'text':'Spark data partition processing'}, {'segment_id':1,'text':'unmatchedwordxyz'}]}}]
        try:
            rows=list(rank_links(spark,docs,episodes))
            self.assertEqual(len(rows),2)
            self.assertEqual({r['source'] for r in rows},{'stackexchange','arxiv'})
            self.assertTrue(all(0<r['score']<=1.000001 and r['shared_terms'] and r['segment_id']==0 for r in rows))
        finally: spark.stop()


class LinkingStoreTests(unittest.TestCase):
    def setUp(self):
        self.db=mongomock.MongoClient().test
        self.doc={'source':'arxiv','doc_id':'1','title':'Spark','body_text':'Spark data','source_url':'https://example.org','mining':{'batch_id':'b'}}
        self.episode={'episode_id':'e','feed_id':'f','batch_id':'pb','status':'ready','analysis':{'segments':[{'segment_id':0,'text':'Spark'}]}}
        self.db.documents.insert_one(dict(self.doc));self.db.podcast_episodes.insert_one(dict(self.episode))
        self.db.linking_runs.insert_one({'run_id':'r','status':'success','episodes':[{'episode_id':'e','fingerprint':episode_fingerprint(self.episode)}]})
        self.db.linking_state.insert_one({'_id':'current','run_id':'r'})
        self.db.cross_source_links.insert_one({'run_id':'r','episode_id':'e','segment_id':0,'source':'arxiv','doc_id':'1',
            'document_fingerprint':document_fingerprint(self.doc),'score':.9,'shared_terms':['spark']})

    def test_stale_or_deleted_documents_hidden(self):
        self.assertEqual(len(for_episode(self.db,self.episode)['items']),1)
        self.db.documents.update_one({}, {'$set':{'body_text':'changed'}})
        self.assertEqual(for_episode(self.db,self.episode)['items'],[])
        changed={**self.episode,'batch_id':'next'}
        self.assertEqual(for_episode(self.db,changed)['status'],'stale')
        self.db.documents.delete_many({})
        self.assertEqual(for_episode(self.db,self.episode)['items'],[])

    def test_pending_and_empty_are_distinct(self):
        self.db.cross_source_links.delete_many({})
        self.assertEqual(for_episode(self.db,self.episode)['status'],'empty')
        self.db.linking_state.delete_many({})
        self.assertEqual(for_episode(self.db,self.episode)['status'],'pending')

    def test_limits_fail_without_replacing_current_run(self):
        with self.assertRaisesRegex(ValueError,'document_limit'):
            run(self.db,None,max_documents=0)
        with self.assertRaisesRegex(ValueError,'segment_limit'):
            run(self.db,None,max_segments=0)
        self.assertEqual(self.db.linking_state.find_one({'_id':'current'})['run_id'],'r')

    def test_failed_run_does_not_publish(self):
        fake=type('Spark',(),{'sparkContext':type('Context',(),{'applicationId':'test','master':'test'})()})()
        with patch('knowpipe.linking.job.rank_links',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): run(self.db,fake)
        self.assertEqual(self.db.linking_state.find_one({'_id':'current'})['run_id'],'r')
        self.assertEqual(self.db.linking_runs.count_documents({'status':'failed'}),1)

    def test_api_preserves_subscription_ownership(self):
        app=create_app(db=self.db,config={'CSRF_ENABLED':False})
        self.db.users.insert_one({'user_id':'u','username':'u'})
        client=app.test_client()
        self.assertEqual(client.get('/api/podcasts/episodes/e').status_code,401)
        with client.session_transaction() as session: session['user_id']='u'
        self.assertEqual(client.get('/api/podcasts/episodes/e').status_code,404)
        self.db.podcast_subscriptions.insert_one({'user_id':'u','feed_id':'f'})
        response=client.get('/api/podcasts/episodes/e')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['cross_source']['items'][0]['doc_id'],'1')
