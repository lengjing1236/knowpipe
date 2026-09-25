"""Deployment guards without starting Java or consuming cluster resources."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from knowpipe.recommendations.runtime import create_spark, runtime_configuration, inspect_partition, validate_probe


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_and_submit_master_are_not_overwritten(self):
        self.assertEqual(runtime_configuration(self.root, {}, {})['spark.master'], 'local[2]')
        config = runtime_configuration(self.root, {}, {'spark.master': 'local[1]', 'spark.sql.shuffle.partitions': '7'})
        self.assertEqual(config['spark.master'], 'local[1]')
        self.assertEqual(config['spark.sql.shuffle.partitions'], '7')

    def test_explicit_env_master_and_partitions_override_submit(self):
        config = runtime_configuration(self.root, {'SPARK_MASTER': 'local[3]', 'LEARNING_SPARK_PARTITIONS': '4'},
                                       {'spark.master': 'local[1]', 'spark.sql.shuffle.partitions': '7'})
        self.assertEqual(config['spark.master'], 'local[3]')
        self.assertEqual(config['spark.sql.shuffle.partitions'], '4')

    def test_create_loads_submit_config_after_gateway_initialization(self):
        from pyspark import SparkContext
        from pyspark.sql import SparkSession
        events = []
        def submitted():
            self.assertEqual(events, ['gateway'])
            conf = MagicMock()
            conf.getAll.return_value = [('spark.master', 'local[1]')]
            return conf
        builder = MagicMock()
        builder.appName.return_value = builder
        builder.config.return_value = builder
        with patch.object(SparkContext, '_ensure_initialized', side_effect=lambda: events.append('gateway')), \
             patch.object(SparkContext, '_active_spark_context', None), \
             patch('pyspark.SparkConf', side_effect=submitted), \
             patch.object(SparkSession, 'builder', builder):
            create_spark('test', self.root, {})
        builder.config.assert_any_call('spark.master', 'local[1]')

    def test_standalone_requires_shared_absolute_path_and_driver_address(self):
        env = {'SPARK_MASTER': 'spark://127.0.0.1:17077'}
        with self.assertRaisesRegex(ValueError, 'spark_shared_root_required'):
            runtime_configuration(self.root, env, {})
        env['KNOWPIPE_SPARK_SHARED_ROOT'] = 'relative'
        with self.assertRaisesRegex(ValueError, 'spark_shared_root_absolute_required'):
            runtime_configuration(self.root, env, {})
        env['KNOWPIPE_SPARK_SHARED_ROOT'] = str(self.root)
        with self.assertRaisesRegex(ValueError, 'spark_driver_host_required'):
            runtime_configuration(self.root, env, {})
        env['SPARK_DRIVER_HOST'] = '127.0.0.1'
        config = runtime_configuration(self.root, env, {})
        self.assertEqual(config['spark.driver.host'], '127.0.0.1')
        self.assertEqual(config['spark.master'], env['SPARK_MASTER'])
        with self.assertRaisesRegex(ValueError, 'spark_index_outside_shared_root'):
            runtime_configuration(self.root.parent / 'outside', env, {})

    def test_other_remote_managers_are_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError, 'spark_cluster_manager_unsupported'):
            runtime_configuration(self.root, {'SPARK_MASTER': 'yarn'}, {})

    def test_executor_probe_checks_bytes_dependencies_and_write_visibility(self):
        path = self.root / 'probe.txt'
        path.write_text('immutable corpus marker')
        import hashlib
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        records = list(inspect_partition(iter([1]), str(path), expected, ['knowpipe.recommendations.text', 'jieba']))
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['read_matches'])
        self.assertTrue(records[0]['write_ok'])
        self.assertTrue(records[0]['dependencies']['jieba']['available'])
        validate_probe(records, str(self.root))
        self.assertFalse(list(self.root.glob('executor-*')))

    def test_probe_fails_closed_on_wrong_bytes_or_missing_dependency(self):
        path = self.root / 'probe.txt'
        path.write_text('one')
        records = list(inspect_partition(iter([1]), str(path), 'wrong', ['not_a_real_dependency_010']))
        self.assertFalse(records[0]['read_matches'])
        self.assertFalse(records[0]['dependencies']['not_a_real_dependency_010']['available'])
        with self.assertRaisesRegex(RuntimeError, 'spark_executor_preflight_failed'):
            validate_probe(records, str(self.root))

    def test_probe_rejects_a_different_algorithm_code_fingerprint(self):
        import hashlib
        path = self.root / 'probe.txt'
        path.write_text('same shared bytes')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records = list(inspect_partition(iter([1]), str(path), digest, ['knowpipe.recommendations.text']))
        self.assertTrue(records[0]['read_matches'])
        self.assertTrue(records[0]['dependencies']['knowpipe.recommendations.text']['available'])
        with self.assertRaisesRegex(RuntimeError, 'spark_executor_preflight_failed') as caught:
            validate_probe(records, str(self.root), digest,
                           {'knowpipe.recommendations.text': {'code_sha256': 'different-algorithm'}})
        self.assertEqual(caught.exception.report['failed_tasks'], 1)
        self.assertIn('dependency_identity_mismatch:knowpipe.recommendations.text',
                      caught.exception.report['tasks'][0]['validation_issues'])


if __name__ == '__main__':
    unittest.main()
