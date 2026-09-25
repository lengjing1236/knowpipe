import unittest
from unittest.mock import Mock

import mongomock

from knowpipe.learning.providers import TextResult, UnconfiguredProvider
from knowpipe.recommendations.query import prepare_goal, entities_in, entity_evidence, processing_identity


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.provider = Mock(processor_id='query-model-v1')
        self.provider.translate.return_value = TextResult('PostgreSQL transaction isolation', 'en')

    def test_original_and_versioned_cached_translation(self):
        plan = prepare_goal(self.db, 'PostgreSQL 事务隔离', self.provider)
        self.assertEqual(plan['variants'], ['PostgreSQL 事务隔离', 'PostgreSQL transaction isolation'])
        self.assertEqual(plan['status'], 'translated')
        self.assertEqual(prepare_goal(self.db, plan['original'], self.provider), plan)
        self.provider.translate.assert_called_once()
        self.provider.processor_id = 'query-model-v2'
        other = prepare_goal(self.db, plan['original'], self.provider)
        self.assertNotEqual(other['processor_id'], plan['processor_id'])
        self.assertEqual(self.provider.translate.call_count, 2)

    def test_failure_visible_not_cached_or_overwriting_original(self):
        plan = prepare_goal(self.db, '进程调度', UnconfiguredProvider())
        self.assertEqual(plan['status'], 'unavailable')
        self.assertEqual(plan['variants'], ['进程调度'])
        self.provider.translate.return_value = TextResult('partial', 'en', False)
        self.assertEqual(prepare_goal(self.db, '进程调度', self.provider)['status'], 'failed')
        self.assertEqual(self.db.goal_interpretations.count_documents({}), 0)
        self.assertEqual(prepare_goal(self.db, 'process scheduling', self.provider)['status'], 'native')

    def test_entities_require_all_aliases_or_explicit_names(self):
        entities = entities_in('比较 PostgreSQL 和 MySQL 的事务，以及 `read_committed`')
        self.assertEqual([e['name'] for e in entities], ['PostgreSQL', 'MySQL', 'read_committed'])
        evidence = entity_evidence('Postgres transactions', 'MySQL also uses read_committed.', entities)
        self.assertEqual(len(evidence), 3)
        self.assertEqual(evidence[0]['field'], 'title')
        self.assertEqual(entity_evidence('Spring transactions', 'generic transactions', entities), [])
        self.assertEqual(entities_in('事务隔离和进程调度'), [])
        self.assertEqual(entities_in('rediscover pandasaurus'), [])

    def test_pipeline_identity_changes_without_feature_change(self):
        old = processing_identity(self.provider, UnconfiguredProvider())
        self.provider.processor_id = 'query-model-v2'
        self.assertNotEqual(old, processing_identity(self.provider, UnconfiguredProvider()))


if __name__ == '__main__':
    unittest.main()
