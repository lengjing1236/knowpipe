import tempfile
import unittest

import mongomock

from knowpipe.learning.content import publish_fulltext, content_version
from knowpipe.recommendations.index import prepare_snapshot, build_index, load_index
# Historical v4 mechanism tests: v5 product results require semantic features.
from knowpipe.recommendations.lexical_baseline import recommend
from knowpipe.recommendations.query import original_query


HISTORY = 'Redis persistence saves memory data with RDB snapshots. RDB snapshots are periodic backups. Redis RDB recovery loads the snapshot.'
EXTRA = 'Redis persistence uses AOF append only logging. AOF fsync policies choose durability and latency. Redis recovery can replay the operation log after a crash.'


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master('local[2]').appName('knowpipe-recommendation-test')
                     .config('spark.sql.shuffle.partitions', '2').getOrCreate())
        cls.spark.sparkContext.setLogLevel('ERROR')
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = mongomock.MongoClient().db
        examples = [('a', 'history', HISTORY), ('b', 'mirror', HISTORY), ('b', 'extra', EXTRA),
                    ('a', 'kafka-known', 'Kafka delivery sends messages through partitions. Consumer offsets track processing progress and replay messages.'),
                    ('b', 'kafka-different', 'Kafka delivery provides networking architecture protocol topology routers sockets packet framing transport.'),
                    ('c', 'unrelated', 'A garden contains roses, tulips, soil and flowers. Novel gardening tools water plants.'),
                    ('c', 'ambiguous', '提交异步任务会进入线程池。线程池调度执行任务。'),
                    ('c', 'intro-en', 'Welcome to the AWS podcast. Thanks for listening and subscribing.'),
                    ('c', 'intro', '欢迎收听 今天我们 感谢订阅')]
        for source, doc_id, body in examples:
            cls.db.documents.insert_one({'source': source, 'doc_id': doc_id, 'title': 'Redis persistence',
                                         'body_text': body, 'language': 'en', 'source_url': 'https://example.com'})
            publish_fulltext(cls.db, source, doc_id, body, 'en')
        cls.db.documents.insert_one({'source': 'arxiv', 'doc_id': 'summary', 'body_text': HISTORY, 'language': 'en'})
        for doc_id, title, body, language in (
            ('pg', 'Postgres transaction isolation', 'Postgres transaction isolation includes read committed and serializable levels.', 'en'),
            ('spring', 'Spring transaction isolation', 'Spring transaction isolation includes read committed and serializable levels.', 'en'),
            ('both', 'Postgres and MySQL', 'Postgres and MySQL transaction isolation differs in default isolation levels.', 'en'),
            ('os-zh', '操作系统调度', '操作系统调度依照优先级分配处理器时间。操作系统调度也要兼顾公平性。', 'zh'),
            ('os-en', 'Operating system scheduling', 'Operating system scheduling allocates processor time with fairness and priorities.', 'en'),
        ):
            cls.db.documents.insert_one({'source': 'fixture', 'doc_id': doc_id, 'title': title})
            publish_fulltext(cls.db, 'fixture', doc_id, body, language)
        cls.snapshot = prepare_snapshot(cls.db, cls.temp.name)
        build_index(cls.spark, cls.snapshot)
        cls.index = load_index(cls.spark, cls.snapshot)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.spark.stop()
        cls.temp.cleanup()

    def test_goal_relevance_and_history_change_have_real_evidence(self):
        first = recommend(self.spark, self.index, 'Redis 持久化', [])
        self.assertTrue(first['items'])
        self.assertNotIn('unrelated', [item['doc_id'] for item in first['items']])
        self.assertNotIn('intro', [item['doc_id'] for item in first['items']])
        self.assertNotIn('summary', [item['doc_id'] for item in first['items']])
        self.assertEqual(first['history_used'], 0)
        self.assertTrue(all(not item['supplement_eligible'] for item in first['items']))
        self.assertTrue(all(item['supplement_evidence']['status'] == 'insufficient_history' for item in first['items']))
        self.assertLess(len(first['items']), len(first['baseline']))
        self.assertGreater(len(first['without_diversity']), len(first['items']))
        version = content_version(self.db.documents.find_one({'doc_id': 'history'}))
        second = recommend(self.spark, self.index, 'Redis 持久化', [
            {'source': 'a', 'doc_id': 'history', 'content_version': version}])
        self.assertEqual(second['history_used'], 1)
        self.assertEqual(second['items'][0]['doc_id'], 'extra')
        extra = second['items'][0]
        self.assertTrue(extra['supplement_eligible'])
        supplement = extra['supplement_evidence']
        self.assertEqual(supplement['status'], 'lexical_candidate')
        self.assertIn('recovery', supplement['shared_context_terms'])
        self.assertIn('aof', supplement['additional_terms'])
        self.assertNotIn('redis', supplement['additional_terms'])
        self.assertNotIn('未知知识', supplement['limitation'])
        candidate = supplement['candidate']
        self.assertEqual(candidate['text'], EXTRA[candidate['start']:candidate['end']])
        comparison = supplement['comparison']
        self.assertEqual(comparison['text'], HISTORY[comparison['start']:comparison['end']])
        self.assertNotIn('mirror', [item['doc_id'] for item in second['items']])
        self.assertTrue(second['baseline'])
        self.assertGreater(len(second['without_history']), len(second['items']))
        for item in first['items'] + second['items']:
            ev = item['goal_evidence']
            original = self.db.documents.find_one({'source': item['source'], 'doc_id': item['doc_id']})['body_text']
            self.assertEqual(ev['text'], original[ev['start']:ev['end']])
            self.assertGreater(item['relevance'], 0)
            self.assertLessEqual(item['relevance'], 1.000001)

    def test_oov_and_stale_history_do_not_invent_results(self):
        self.assertEqual(recommend(self.spark, self.index, 'unmatchedxyzzzz', [])['items'], [])
        result = recommend(self.spark, self.index, 'Redis persistence', [
            {'source': 'a', 'doc_id': 'history', 'content_version': 'old'}])
        self.assertEqual(result['history_used'], 0)
        self.assertEqual(result['history_unavailable'], 1)
        self.assertTrue(all(not item['supplement_eligible'] for item in result['items']))
        self.assertEqual(recommend(self.spark, self.index, '数据库 事务 提交 回滚', [])['items'], [])
        self.assertEqual(recommend(self.spark, self.index, 'AWS', [])['items'], [])

    def test_bilingual_branches_and_all_explicit_objects(self):
        goal = '操作系统调度'
        plan = original_query(goal)
        plan.update(variants=[goal, 'operating system scheduling'], status='translated')
        result = recommend(self.spark, self.index, goal, [], query_plan=plan)
        self.assertTrue({'os-zh', 'os-en'}.issubset({i['doc_id'] for i in result['items']}))
        pg = recommend(self.spark, self.index, 'PostgreSQL transaction isolation', [])
        self.assertTrue(pg['items'])
        self.assertNotIn('spring', [i['doc_id'] for i in pg['items']])
        self.assertTrue(all(i['entity_evidence'][0]['name'] == 'PostgreSQL' for i in pg['items']))
        both = recommend(self.spark, self.index, 'Postgres MySQL transaction isolation', [])
        self.assertEqual([i['doc_id'] for i in both['items']], ['both'])
        self.assertEqual(len(both['items'][0]['entity_evidence']), 2)

    def test_new_words_without_shared_context_are_not_supplement_evidence(self):
        version = content_version(self.db.documents.find_one({'doc_id': 'kafka-known'}))
        result = recommend(self.spark, self.index, 'Kafka delivery', [
            {'source': 'a', 'doc_id': 'kafka-known', 'content_version': version}])
        item = next(item for item in result['items'] if item['doc_id'] == 'kafka-different')
        self.assertFalse(item['supplement_eligible'])
        self.assertEqual(item['supplement_evidence']['status'], 'insufficient_evidence')
        self.assertEqual(item['supplement_evidence']['shared_context_terms'], [])

    def test_empty_fulltext_snapshot_and_reusable_immutable_cache(self):
        empty = prepare_snapshot(mongomock.MongoClient().empty, self.temp.name)
        build_index(self.spark, empty)
        index = load_index(self.spark, empty)
        try:
            result = recommend(self.spark, index, '数据库', [])
            self.assertEqual(result['reason'], 'no_fulltext')
            self.assertEqual(result['items'], [])
        finally:
            index.close()
        repeated = prepare_snapshot(self.db, self.temp.name)
        build_index(self.spark, repeated)
        self.assertEqual(repeated['corpus_id'], self.snapshot['corpus_id'])
        self.assertEqual(repeated['index_path'], self.snapshot['index_path'])
