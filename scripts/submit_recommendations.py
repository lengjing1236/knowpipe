#!/usr/bin/env python3
"""Package first-party Python code and submit the worker or preflight to Spark.

Native dependencies are deliberately not bundled: install the same pinned
requirements/Python environment on each executor; the preflight checks them.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_code(directory):
    archive = Path(directory) / 'knowpipe.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / 'knowpipe').rglob('*.py')):
            bundle.write(path, path.relative_to(ROOT))
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', default=os.environ.get('SPARK_MASTER', 'local[2]'))
    parser.add_argument('--index-root', required=True)
    parser.add_argument('--driver-host')
    parser.add_argument('--driver-memory', default='512m')
    parser.add_argument('--executor-memory', default='512m')
    parser.add_argument('--total-executor-cores', type=int, default=2)
    parser.add_argument('--conf', action='append', default=[], help='Additional Spark key=value; repeat as needed')
    parser.add_argument('--preflight-output', help='Run preflight only and save its evidence')
    parser.add_argument('worker_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.total_executor_cores < 1:
        parser.error('--total-executor-cores must be positive')
    if any('=' not in item or item.startswith('spark.master=') for item in args.conf):
        parser.error('--conf requires key=value; select the master with --master')
    command = shutil.which('spark-submit')
    if not command:
        parser.error('spark-submit is unavailable in PATH')
    env = os.environ.copy()
    # The CLI --master is the submit configuration; do not allow an inherited
    # SPARK_MASTER to silently override this explicit request in the application.
    env.pop('SPARK_MASTER', None)
    if args.driver_host:
        env['SPARK_DRIVER_HOST'] = args.driver_host
    with tempfile.TemporaryDirectory(prefix='knowpipe-submit-') as directory:
        archive = package_code(directory)
        command = [command, '--master', args.master, '--deploy-mode', 'client', '--py-files', str(archive),
                   '--driver-memory', args.driver_memory, '--executor-memory', args.executor_memory,
                   '--executor-cores', '1', '--total-executor-cores', str(args.total_executor_cores)]
        for item in args.conf:
            command += ['--conf', item]
        if args.preflight_output:
            command += [str(ROOT / 'scripts/spark_preflight.py'), '--index-root', args.index_root,
                        '--output', args.preflight_output]
        else:
            entry = Path(directory) / 'worker_entry.py'
            entry.write_text('from knowpipe.recommendations.worker import main\nmain()\n', encoding='utf-8')
            remainder = args.worker_args[1:] if args.worker_args[:1] == ['--'] else args.worker_args
            command += [str(entry), '--index-root', args.index_root, *remainder]
        return subprocess.call(command, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
