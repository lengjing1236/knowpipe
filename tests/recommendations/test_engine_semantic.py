"""Real Spark joins/aggregation/selection with explicit deterministic features.

These verify wiring; real-model usefulness is separately evaluated on frozen
public material. The feature stub is never used by production configuration.
"""
import tempfile
import unittest
from types import SimpleNamespace

import mongomock

from knowpipe.learning.content import publish_fulltext, content_version
from knowpipe.recommendations.engine import recommend, _expanded_parts
from knowpipe.recommendations.index import prepare_snapshot, build_index, load_index
from tests.recommendations.test_semantic_analysis import HISTORY, COVERED, EXTRA, Features


class SemanticEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master('local[2]').appName('knowpipe-semantic-wiring-test')
                     .config('spark.sql.shuffle.partitions', '2').getOrCreate())
        cls.spark.sparkContext.setLogLevel('ERROR')
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = mongomock.MongoClient().db
        cls.bodies = {'history': HISTORY, 'copy': COVERED, 'mixed': COVERED + ' ' + EXTRA,
                      'question': 'How can the database recover its most recently committed transactions after a sudden server failure?'}
        for key, body in cls.bodies.items():
            cls.db.documents.insert_one({'source': 'test', 'doc_id': key, 'title': 'Database recovery',
                                         'source_url': 'https://example.test/' + key})
            publish_fulltext(cls.db, 'test', key, body, 'en')
        cls.snapshot = prepare_snapshot(cls.db, cls.temp.name)
        build_index(cls.spark, cls.snapshot)
        cls.index = load_index(cls.spark, cls.snapshot)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.spark.stop()
        cls.temp.cleanup()

    def test_semantic_evidence_controls_spark_selection_and_has_literal_offsets(self):
        version = content_version(self.db.documents.find_one({'doc_id': 'history'}))
        result = recommend(self.spark, self.index, 'database recovery', [
            {'source': 'test', 'doc_id': 'history', 'content_version': version}], semantic=Features())
        self.assertEqual(result['semantic']['status'], 'ready')
        self.assertEqual([item['doc_id'] for item in result['items']], ['mixed'])
        item = result['items'][0]
        self.assertTrue(item['supplement_eligible'])
        self.assertEqual(item['comparison']['status'], 'possible_supplement')
        self.assertGreater(item['supplement_score'], 0.)
        for name in ('candidate', 'history'):
            evidence = item['comparison'][name]
            self.assertEqual(evidence['text'], self.bodies[evidence['doc_id']][evidence['start']:evidence['end']])
        self.assertTrue(result['without_history'])

    def test_seed_document_expansion_includes_nonmatching_neighbor_within_total_budget(self):
        text = 'A bounded permit rejects an excessive release when its counter would exceed the initial value.'
        paragraphs = self.spark.createDataFrame([
            ('doc', 'seed', 0, len(HISTORY), HISTORY),
            ('doc', 'neighbor', 200, 200 + len(text), text)],
            'doc_key string, pid string, start int, end int, text string')
        available = self.spark.createDataFrame([('doc', 'seed', 0, len(HISTORY), .4, .5, ['database'])],
            'doc_key string, pid string, start int, end int, relevance double, query_coverage double, matched_terms array<string>')
        candidates = self.spark.createDataFrame([('doc', .4)], 'doc_key string, relevance double')
        expanded, quota = _expanded_parts(SimpleNamespace(paragraphs=paragraphs), available, candidates)
        self.assertEqual(quota, 300)
        self.assertEqual({row.pid for row in expanded}, {'seed', 'neighbor'})
        neighbor = next(row for row in expanded if row.pid == 'neighbor')
        self.assertEqual(neighbor.matched_terms, [])
        self.assertEqual(neighbor.start, 200)


if __name__ == '__main__':
    unittest.main()
