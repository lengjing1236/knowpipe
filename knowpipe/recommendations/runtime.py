"""Explicit local/Standalone configuration and bounded real-executor checks.

The current snapshot/index implementation uses POSIX paths. Standalone therefore
requires the same absolute, writable shared mount on driver and every executor.
Declaring that mount is not proof: ``preflight`` writes/reads a marker in tasks.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import platform
import socket
import time
import urllib.request
import uuid
from pathlib import Path


DEPENDENCIES = ('knowpipe.recommendations.text', 'knowpipe.recommendations.engine',
                'knowpipe.recommendations.query', 'knowpipe.learning.providers',
                'knowpipe.learning.content', 'knowpipe.learning.quality', 'jieba', 'pyspark')


class ExecutorPreflightError(RuntimeError):
    def __init__(self, report):
        super().__init__('spark_executor_preflight_failed')
        self.report = report


def runtime_configuration(index_root, env=None, submitted=None):
    """Return only applicable settings; explicit environment beats spark-submit."""
    env = os.environ if env is None else env
    config = dict(submitted or {})
    master = env.get('SPARK_MASTER') or config.get('spark.master') or 'local[2]'
    config['spark.master'] = master
    if not (master == 'local' or master.startswith('local[') or master.startswith('spark://')):
        raise ValueError('spark_cluster_manager_unsupported')
    if env.get('LEARNING_SPARK_PARTITIONS'):
        partitions = int(env['LEARNING_SPARK_PARTITIONS'])
        if not 1 <= partitions <= 4096:
            raise ValueError('spark_partition_count_invalid')
        config['spark.sql.shuffle.partitions'] = str(partitions)
    else:
        config.setdefault('spark.sql.shuffle.partitions', '16')
    config.setdefault('spark.sql.adaptive.coalescePartitions.enabled', 'false')
    # Small compressed Parquet files can expand into a large cache block when
    # Spark combines them into its default 128 MiB input partition.
    config.setdefault('spark.sql.files.maxPartitionBytes', str(16 * 1024 * 1024))
    for key, name in (('SPARK_DRIVER_HOST', 'spark.driver.host'),
                      ('SPARK_DRIVER_BIND_ADDRESS', 'spark.driver.bindAddress')):
        if env.get(key):
            config[name] = env[key]
    if master.startswith('spark://'):
        shared = env.get('KNOWPIPE_SPARK_SHARED_ROOT')
        if not shared:
            raise ValueError('spark_shared_root_required')
        if not Path(shared).is_absolute():
            raise ValueError('spark_shared_root_absolute_required')
        root = Path(index_root).resolve()
        if not root.is_relative_to(Path(shared).resolve()):
            raise ValueError('spark_index_outside_shared_root')
        if not config.get('spark.driver.host'):
            raise ValueError('spark_driver_host_required')
    return config


def create_spark(app_name, index_root, env=None):
    from pyspark import SparkConf, SparkContext
    from pyspark.sql import SparkSession
    env = os.environ if env is None else env
    # Before the gateway exists, Python SparkConf is an empty local dictionary.
    # Start the gateway (not a SparkContext) so spark-submit's JVM properties
    # are visible before choosing a fallback master. A pure helper test cannot
    # prove this boundary; native Standalone acceptance covers it as well.
    SparkContext._ensure_initialized()
    submitted = dict(SparkConf().getAll())
    active = SparkContext._active_spark_context
    if active is not None:
        submitted.update(dict(active.getConf().getAll()))
    config = runtime_configuration(index_root, env, submitted)
    if active is not None and active.master != config['spark.master']:
        raise RuntimeError('spark_active_master_mismatch')
    builder = SparkSession.builder.appName(app_name)
    for key, value in config.items():
        builder = builder.config(key, value)
    # The Python gateway's default "pyspark-shell" name is not our application.
    builder = builder.appName(app_name)
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel('WARN')
    if config['spark.master'].startswith('spark://'):
        try:
            spark._knowpipe_preflight = preflight(spark, index_root)
        except Exception:
            # Do not leave an unusable cluster context alive after startup fails.
            if active is None:
                spark.stop()
            raise
    return spark


def _dependency(name):
    try:
        module = importlib.import_module(name)
        result = {'available': True, 'version': str(getattr(module, '__version__', 'unknown'))}
        if name.startswith('knowpipe.'):
            result['code_sha256'] = hashlib.sha256(inspect.getsource(module).encode()).hexdigest()
        return result
    except Exception as error:
        return {'available': False, 'error_type': type(error).__name__}


def _java_ancestor():
    """Linux-only process identity; read names/PPIDs, never command arguments."""
    pid = os.getppid()
    for _ in range(6):
        try:
            status = (Path('/proc') / str(pid) / 'status').read_text()
            fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
            if fields.get('Name', '').strip() == 'java':
                return pid
            pid = int(fields.get('PPid', '0').strip())
            if not pid:
                break
        except (OSError, ValueError):
            break
    return None


def inspect_partition(rows, probe_path, expected_hash, dependencies=DEPENDENCIES):
    """Executed in Python workers, not on the driver; one small record/partition."""
    count = sum(1 for _ in rows)
    path = Path(probe_path)
    record = {'host': socket.gethostname(), 'python_pid': os.getpid(), 'parent_pid': os.getppid(),
              'executor_jvm_pid': _java_ancestor(),
              'python_version': platform.python_version(), 'rows': count,
              'read_matches': False, 'write_ok': False,
              'dependencies': {name: _dependency(name) for name in dependencies}}
    try:
        from pyspark import TaskContext
        task = TaskContext.get()
        if task:
            record.update(partition_id=task.partitionId(), task_attempt_id=task.taskAttemptId())
        record['read_matches'] = hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        write_name = 'executor-' + uuid.uuid4().hex
        write_path = path.parent / write_name
        write_path.write_text(expected_hash, encoding='ascii')
        record.update(write_ok=True, write_name=write_name)
    except OSError as error:
        record['error_type'] = type(error).__name__
    # Keep tiny tasks running briefly so independent executors can both take work.
    time.sleep(.1)
    yield record


def validate_probe(records, directory, expected_hash=None, expected_dependencies=None):
    failures = []
    for record in records:
        issues = []
        ok = record['read_matches'] and record['write_ok']
        if not record['read_matches']:
            issues.append('shared_read_mismatch')
        if not record['write_ok']:
            issues.append('executor_write_failed')
        ok = ok and all(item.get('available') for item in record['dependencies'].values())
        if not all(item.get('available') for item in record['dependencies'].values()):
            issues.append('dependency_unavailable')
        if record.get('python_version', '').split('.')[:2] != platform.python_version().split('.')[:2]:
            ok = False
            issues.append('python_version_mismatch')
        for name, expected in (expected_dependencies or {}).items():
            actual = record['dependencies'].get(name, {})
            if any(actual.get(key) != expected.get(key) for key in ('version', 'code_sha256') if key in expected):
                ok = False
                issues.append('dependency_identity_mismatch:' + name)
        name = record.get('write_name', '')
        if name and Path(name).name == name:
            path = Path(directory) / name
            try:
                observed = path.read_text(encoding='ascii')
                if expected_hash is not None and observed != expected_hash:
                    ok = False
                    issues.append('executor_marker_mismatch')
            except OSError:
                ok = False
                issues.append('executor_marker_not_visible_to_driver')
            finally:
                path.unlink(missing_ok=True)
        else:
            ok = False
            issues.append('executor_marker_missing')
        if not ok:
            record['validation_issues'] = issues
            failures.append(record)
    if not records or failures:
        raise ExecutorPreflightError({'status': 'failed', 'failed_tasks': len(failures), 'tasks': records})


def executor_statistics(spark):
    """Spark's own executor IDs/task counts supplement Python-process evidence."""
    sc = spark.sparkContext
    if not sc.uiWebUrl:
        return {'available': False, 'reason': 'spark_ui_disabled', 'executors': []}
    try:
        url = f'{sc.uiWebUrl}/api/v1/applications/{sc.applicationId}/executors'
        with urllib.request.urlopen(url, timeout=10) as response:
            rows = json.load(response)
        keys = ('id', 'hostPort', 'isActive', 'totalCores', 'totalTasks', 'completedTasks', 'failedTasks',
                'totalDuration', 'maxMemory', 'memoryUsed')
        return {'available': True, 'executors': [{k: row[k] for k in keys if k in row} for row in rows]}
    except Exception as error:
        return {'available': False, 'reason': type(error).__name__, 'executors': []}


def preflight(spark, index_root, partitions=8):
    if not 1 <= partitions <= 32:
        raise ValueError('spark_preflight_partition_limit')
    root = Path(index_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='spark-preflight-', dir=root) as directory:
        path = Path(directory) / 'driver-marker'
        path.write_bytes(os.urandom(128))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        started = time.monotonic()
        records = spark.sparkContext.parallelize(range(partitions), partitions).mapPartitions(
            lambda rows: inspect_partition(rows, str(path), digest)).collect()
        validate_probe(records, directory, digest, {name: _dependency(name) for name in DEPENDENCIES})
    hosts = sorted({record['host'] for record in records} | {socket.gethostname()})
    return {'status': 'passed', 'master': spark.sparkContext.master,
            'application_id': spark.sparkContext.applicationId,
            'driver': {'host': socket.gethostname(), 'pid': os.getpid(), 'python_version': platform.python_version()},
            'shared_root': str(root), 'task_count': len(records), 'tasks': records,
            'observed_hosts': hosts, 'single_host_observed': len(hosts) == 1,
            'multi_host_acceptance': 'not_established',
            'seconds': round(time.monotonic() - started, 3), **executor_statistics(spark)}
