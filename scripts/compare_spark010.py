#!/usr/bin/env python3
"""Run local[2] then two real Standalone executors on this one physical host.

Starts only task-owned foreground Java process groups and always stops them.
Requires roughly 3 GiB free memory; run serially with other model/Spark jobs.
This is a deployment/consistency test, not a multi-host speed benchmark.
"""
import argparse
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from submit_recommendations import package_code


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_port(number, process, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('spark_daemon_exited')
        try:
            with socket.create_connection(('127.0.0.1', number), timeout=.5):
                return
        except OSError:
            time.sleep(.2)
    raise TimeoutError('spark_daemon_start_timeout')


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def compare(local, standalone, tolerance=1e-10):
    checks = {}
    checks['successful_runs'] = local['status'] == standalone['status'] == 'passed'
    checks['identical_input'] = local['input_sha256'] == standalone['input_sha256']
    checks['identical_paragraphs'] = local['paragraphs_sha256'] == standalone['paragraphs_sha256']
    checks['identical_feature_identity'] = local['snapshot']['feature_id'] == standalone['snapshot']['feature_id']
    max_delta = 0.
    for key, dimensions, value in [('terms', ('term',), 'idf'), ('postings', ('pid', 'term'), 'weight')]:
        left, right = local[key], standalone[key]
        dimensions_equal = len(left) == len(right) and all(
            tuple(a[k] for k in dimensions) == tuple(b[k] for k in dimensions) for a, b in zip(left, right))
        deltas = [abs(a[value] - b[value]) for a, b in zip(left, right)]
        max_delta = max([max_delta, *deltas])
        checks[key + '_equal_with_tolerance'] = dimensions_equal and all(d <= tolerance for d in deltas)
    ranks_equal = True
    for left, right in zip(local['recommendations'], standalone['recommendations']):
        a, b = left['result']['items'], right['result']['items']
        ranks_equal &= [(v['source'], v['doc_id']) for v in a] == [(v['source'], v['doc_id']) for v in b]
        for av, bv in zip(a, b):
            for field in ('selection_score', 'relevance', 'history_overlap'):
                if field in av and field in bv:
                    delta = abs(av[field] - bv[field])
                    max_delta = max(max_delta, delta)
                    ranks_equal &= delta <= tolerance
            ranks_equal &= av['goal_evidence'] == bv['goal_evidence']
    checks['identical_recommendations_with_tolerance'] = ranks_equal and len(local['recommendations']) == len(standalone['recommendations'])
    executors = [e for e in standalone['runtime'].get('executors', []) if e['id'] != 'driver' and e.get('completedTasks', 0) > 0]
    checks['two_real_executors_completed_tasks'] = len(executors) >= 2
    checks['native_standalone_master'] = standalone['runtime']['master'].startswith('spark://')
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'absolute_float_tolerance': tolerance, 'max_absolute_float_delta': max_delta,
            'physical_computers': 1, 'deployment': 'single-host native Standalone, two executor JVMs',
            'multi_host_acceptance': 'pending_resources',
            'note': 'Synthetic 12-document consistency test. Timings include startup; not a scale or speedup claim.',
            'seconds': {'local': local['seconds'], 'standalone': standalone['seconds']},
            'index_seconds': {'local': local['index_seconds'], 'standalone': standalone['index_seconds']},
            'standalone_executors': executors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='state/feature010/spark-comparison')
    parser.add_argument('--output', default='evidence/010-quality-cluster-readiness/spark')
    args = parser.parse_args()
    state, output = Path(args.state).resolve(), Path(args.output).resolve()
    state.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    import pyspark
    spark_class = Path(pyspark.__file__).parent / 'bin/spark-class'
    spark_submit = shutil.which('spark-submit')
    if not spark_submit or not spark_class.is_file():
        raise RuntimeError('spark_binaries_unavailable')
    env = os.environ.copy()
    env.pop('SPARK_MASTER', None)
    env.update(SPARK_LOCAL_IP='127.0.0.1', SPARK_DRIVER_HOST='127.0.0.1',
               SPARK_DRIVER_BIND_ADDRESS='127.0.0.1', KNOWPIPE_SPARK_SHARED_ROOT=str(state),
               SPARK_DAEMON_MEMORY='128m', LEARNING_SPARK_PARTITIONS='2',
               PYSPARK_PYTHON=sys.executable, PYSPARK_DRIVER_PYTHON=sys.executable)
    archive = package_code(state)
    processes, handles = [], []
    result = {'status': 'failed', 'multi_host_acceptance': 'pending_resources'}
    def run_application(master, label):
        # A fresh index directory prevents accidental reuse of local artifacts.
        index_root = state / (label + '-' + str(time.time_ns()))
        command = [spark_submit, '--master', master, '--deploy-mode', 'client', '--py-files', str(archive),
                   '--driver-memory', '512m', '--executor-memory', '512m', '--executor-cores', '1',
                   '--total-executor-cores', '2', '--conf', 'spark.ui.retainedJobs=30',
                   '--conf', 'spark.ui.retainedStages=60', '--conf', 'spark.sql.ui.retainedExecutions=30',
                   '--conf', 'spark.executorEnv.PYSPARK_PYTHON=' + sys.executable,
                   str(ROOT / 'scripts/acceptance_spark010.py'), '--index-root', str(index_root),
                   '--output', str(output / (label + '.json'))]
        with (output / (label + '.log')).open('w') as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            try:
                code = process.wait(timeout=900)
            except BaseException:
                stop(process)
                raise
        if code:
            raise RuntimeError(label + '_acceptance_failed')
        return json.loads((output / (label + '.json')).read_text())
    try:
        local = run_application('local[2]', 'local')
        master_port, master_ui, worker_port, worker_ui = (port() for _ in range(4))
        master_url = 'spark://127.0.0.1:' + str(master_port)
        for label, command, ready_port in [
            ('master', [str(spark_class), 'org.apache.spark.deploy.master.Master', '--host', '127.0.0.1',
                        '--port', str(master_port), '--webui-port', str(master_ui)], master_port),
            ('worker', [str(spark_class), 'org.apache.spark.deploy.worker.Worker', '--host', '127.0.0.1',
                        '--port', str(worker_port), '--webui-port', str(worker_ui), '--cores', '2',
                        '--memory', '1200M', '--work-dir', str(state / 'worker'), master_url], worker_ui)]:
            log = (output / (label + '.log')).open('w')
            handles.append(log)
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            processes.append(process)
            wait_port(ready_port, process)
        deadline = time.monotonic() + 30
        while True:
            with urllib.request.urlopen(f'http://127.0.0.1:{master_ui}/json/', timeout=3) as response:
                master_status = json.load(response)
            if master_status.get('aliveworkers', 0) >= 1:
                break
            if time.monotonic() > deadline:
                raise TimeoutError('spark_worker_registration_timeout')
            time.sleep(.3)
        standalone = run_application(master_url, 'standalone')
        result = compare(local, standalone)
        result['master_worker_status'] = {key: master_status.get(key) for key in ('aliveworkers', 'cores', 'memory')}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['status'] != 'passed':
            raise RuntimeError('spark_consistency_comparison_failed')
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error)[:300])
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for handle in handles:
            handle.close()
        (output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
