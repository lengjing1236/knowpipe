#!/usr/bin/env python3
"""Prepare and run the honest Chinese course demo, using one real Spark worker.

Example: python3 scripts/demo_release.py prepare
         python3 scripts/demo_release.py start
MongoDB must already be running. No existing database is reset or dropped.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.defense_demo import private_json, running, stop as stop_processes
from knowpipe.learning.content import is_chinese
from knowpipe.learning.store import save_goal
from knowpipe.recommendations.importer import import_record, validate_record
from knowpipe.web import auth, mongo_sink

STATE = ROOT / 'state/demo-release'
DEFAULT_GOAL = '学习 Python 的异常捕获、异常传播和日志记录'
LOCAL_TRANSLATION_KEYS = ('KNOWPIPE_LLAMA_TRANSLATION_MODEL_PATH', 'KNOWPIPE_LLAMA_TRANSLATION_URL',
                          'KNOWPIPE_NLLB_MODEL_PATH', 'KNOWPIPE_TRANSLATION_MODEL_PATH',
                          'KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH')


def chinese_corpus(path):
    """All verified Chinese fulltexts in the input, without selecting desired answers."""
    records, digest, total = [], hashlib.sha256(), 0
    with Path(path).open('rb') as stream:
        for line in stream:
            digest.update(line)
            if not line.strip():
                continue
            total += 1
            record = json.loads(line)
            if is_chinese(record.get('language')):
                records.append(validate_record(record))
    if not records:
        raise ValueError('输入中没有经过验证的中文全文。')
    identities = [(record['source'], record['doc_id']) for record in records]
    if len(identities) != len(set(identities)):
        raise ValueError('中文输入有重复文档标识，请先核对来源版本。')
    return records, {'input_sha256': digest.hexdigest(), 'input_document_count': total,
                     'document_count': len(records), 'sources': dict(Counter(r['source'] for r in records)),
                     'scope': '输入导出中的全部已验证中文全文；小子集工程演示，不代表万条背景推荐质量。'}


def prepare_database(db, state, corpus, goal):
    records, summary = chinese_corpus(corpus)
    marker = db.demo_release.find_one({'_id': 'owner'})
    if marker is None and db.list_collection_names():
        raise ValueError('目标数据库已存在且不属于此演示工具；请选择新的 --database。')
    if marker and marker['input_sha256'] != summary['input_sha256']:
        raise ValueError('输入语料已变化；为保留已有演示记录，请使用新的 --database 和 --state。')
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    db.demo_release.update_one({'_id': 'owner'}, {'$setOnInsert': {
        'input_sha256': summary['input_sha256'], 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    mongo_sink.ensure_indexes(db)
    from knowpipe.learning.store import ensure_indexes
    ensure_indexes(db)
    for record in records:
        import_record(db, record)
    if db.documents.count_documents({}) != len(records):
        raise ValueError('演示数据库另有资料；未删除任何数据，请使用新的演示数据库。')
    account_path = state / 'account.json'
    if account_path.exists():
        account = json.loads(account_path.read_text())
        user = mongo_sink.find_user_by_id(db, account['user_id'])
        if not user or user['username'] != account['username']:
            raise ValueError('私有账户文件与数据库不匹配；未重置账户。')
    else:
        account = {'user_id': uuid.uuid4().hex, 'username': 'course_demo', 'password': secrets.token_urlsafe(15)}
        mongo_sink.create_user(db, account['user_id'], account['username'], auth.hash_password(account['password']))
        private_json(account_path, account)
    profile = mongo_sink.get_or_create_profile(db, account['user_id'])
    if not profile.get('learning_goal'):
        save_goal(db, account['user_id'], goal)
    summary.update(database=db.name, goal=(mongo_sink.get_or_create_profile(db, account['user_id']).get('learning_goal') or {}).get('text'),
                   account_file=str(account_path), ready_job_injection=False,
                   preparation='仅导入全文、建立演示账户和保存目标；推荐结果由真实后台计算。',
                   documents=[{'source': r['source'], 'doc_id': r['doc_id'], 'title': r['title'],
                               'source_url': r['source_url']} for r in records])
    private_json(state / 'preparation.json', summary)
    return summary


def worker_environment(base, *, state, mongo_uri, database, semantic_root,
                       api_key_file=ROOT / 'state/demo012/bigmodel-api-key'):
    env = {key: value for key, value in base.items() if key not in LOCAL_TRANSLATION_KEYS}
    if not env.get('KNOWPIPE_BIGMODEL_API_KEY') and Path(api_key_file).is_file():
        env['KNOWPIPE_BIGMODEL_API_KEY'] = Path(api_key_file).read_text().strip()
    secret_path = state / 'secret-key'
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(32))
        secret_path.chmod(0o600)
    env.update(SECRET_KEY=secret_path.read_text().strip(), MONGO_URI=mongo_uri, MONGO_DB=database,
               APP_ENV='development', PUBLIC_ORIGIN='', TRUST_PROXY='0',
               SPARK_LOCAL_IP='127.0.0.1', SPARK_MASTER='local[1]', LEARNING_SPARK_PARTITIONS='4',
               KNOWPIPE_TRANSLATION_PROVIDER='bigmodel-free', KNOWPIPE_SEMANTIC_MODEL_PATH=str(semantic_root),
               KNOWPIPE_MODEL_THREADS='2', PYSPARK_SUBMIT_ARGS='--driver-memory 640m '
               '--conf spark.memory.fraction=0.35 --conf spark.sql.ui.retainedExecutions=10 '
               '--conf spark.ui.retainedJobs=10 --conf spark.ui.retainedStages=20 pyspark-shell')
    return env


def other_spark_processes():
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ')
            if b'org.apache.spark.deploy.SparkSubmit' in command and command.startswith(b'java '):
                found.append(int(entry.name))
            elif b'org.apache.spark.deploy.SparkSubmit' in command and b'/bin/java ' in command:
                found.append(int(entry.name))
        except OSError:
            continue
    return found


def stop_services(manifest):
    # The worker catches SIGINT and releases its database lease in finally.
    # A raw SIGTERM would leave a healthy restart waiting for a 180-second lease.
    workers = [record for record in manifest.get('processes', [])
               if record.get('name') == 'recommendations' and running(record)]
    for record in workers:
        try:
            os.kill(record['pid'], signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while any(running(record) for record in workers) and time.monotonic() < deadline:
        time.sleep(.2)
    stop_processes(manifest)


def start_services(args, db):
    state = args.state
    manifest_path = state / 'services.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if any(running(record) for record in previous.get('processes', [])):
            raise RuntimeError('此演示仍有服务在运行；请先 status，需重启时先 stop。')
    if not (state / 'preparation.json').is_file() or not db.demo_release.find_one({'_id': 'owner'}):
        raise RuntimeError('请先运行 prepare；本工具不会使用未声明的现有数据库。')
    if json.loads((state / 'preparation.json').read_text())['database'] != args.database:
        raise RuntimeError('--state 中的准备记录属于另一个数据库。')
    if other_spark_processes():
        raise RuntimeError('已有其他 Spark 作业运行，等待其完成后再启动演示 worker。')
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', args.port))
        except OSError as exc:
            raise RuntimeError(f'端口 {args.port} 已被占用；未停止其他服务。') from exc
    from knowpipe.recommendations.semantic import LocalSemantic
    LocalSemantic(args.semantic_root).check_available()  # Hash checks only, no model inference.
    env = worker_environment(os.environ, state=state, mongo_uri=args.mongo_uri,
                             database=args.database, semantic_root=args.semantic_root)
    manifest = {'local_url': f'http://127.0.0.1:{args.port}', 'mongo_uri': args.mongo_uri,
                'database': args.database, 'translation_mode': 'bigmodel-free',
                'remote_credentials_present': bool(env.get('KNOWPIPE_BIGMODEL_API_KEY')),
                'processes': [], 'started_at': datetime.now(timezone.utc).isoformat()}

    def launch(name, command):
        with (state / f'{name}.log').open('a') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        record = {'name': name, 'pid': process.pid,
                  'start_ticks': Path(f'/proc/{process.pid}/stat').read_text().split()[21]}
        manifest['processes'].append(record)
        private_json(manifest_path, manifest)

    try:
        launch('web', [sys.executable, '-m', 'gunicorn', '--bind', f'127.0.0.1:{args.port}',
                       '--workers', '1', '--worker-class', 'gthread', '--threads', '4', '--timeout', '60',
                       '--access-logfile', '-', 'knowpipe.web.app:create_app()'])
        launch('recommendations', [sys.executable, '-m', 'knowpipe.recommendations.worker',
                                   '--mongo-uri', args.mongo_uri, '--mongo-db', args.database,
                                   '--index-root', str(state / 'index')])
        for attempt in range(60):
            if not all(running(record) for record in manifest['processes']):
                raise RuntimeError('演示服务退出，请查看私有 state 目录的日志。')
            try:
                with urllib.request.urlopen(manifest['local_url'] + '/health/ready', timeout=1) as response:
                    if response.status == 200:
                        return manifest
            except OSError:
                pass
            time.sleep(.5)
        raise RuntimeError('Web 尚未就绪；请查看 web.log。')
    except BaseException:
        stop_services(manifest)
        raise


def status(state, db):
    manifest_path = state / 'services.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    summary = json.loads((state / 'preparation.json').read_text()) if (state / 'preparation.json').exists() else {}
    if summary and summary.get('database') != db.name:
        raise ValueError('--state 中的准备记录属于另一个数据库；未访问其用户状态。')
    result = {key: summary.get(key) for key in ('database', 'scope', 'document_count', 'sources', 'goal', 'account_file')}
    result.update(local_url=manifest.get('local_url'),
                  services=[{'name': r['name'], 'running': running(r)} for r in manifest.get('processes', [])],
                  translation_mode=manifest.get('translation_mode'),
                  remote_credentials_present=manifest.get('remote_credentials_present', False))
    account_path = state / 'account.json'
    if account_path.exists():
        from knowpipe.recommendations.queue import view
        account = json.loads(account_path.read_text())
        current = view(db, account['user_id'])
        profile = db.user_profiles.find_one({'user_id': account['user_id']}) or {}
        result.update(goal=(profile.get('learning_goal') or {}).get('text'), read_count=len(profile.get('read_doc_ids', [])),
                      recommendation_status=current['status'], semantic_status=(current.get('semantic') or {}).get('status'),
                      corpus=current.get('corpus'),
                      items=[{key: item.get(key) for key in ('source', 'doc_id', 'title', 'chinese_ready', 'rank')}
                             for item in current.get('items', [])])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'start', 'status', 'stop'))
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--database', default='knowpipe_demo012')
    parser.add_argument('--port', type=int, default=8019)
    parser.add_argument('--corpus', type=Path, default=ROOT / 'state/feature009/corpus-expanded/documents.jsonl')
    parser.add_argument('--semantic-root', type=Path, default=ROOT / 'state/feature011/semantic-multilingual')
    parser.add_argument('--goal', default=DEFAULT_GOAL)
    args = parser.parse_args()
    args.state = args.state.resolve()
    if args.command == 'stop':
        manifest_path = args.state / 'services.json'
        if manifest_path.exists():
            stop_services(json.loads(manifest_path.read_text()))
        print('演示服务已停止；MongoDB、资料、账户和真实计算记录均保留。')
        return
    from pymongo import MongoClient
    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        client.admin.command('ping')
        db = client[args.database]
        if args.command == 'prepare':
            result = prepare_database(db, args.state, args.corpus, args.goal)
        else:
            if args.command == 'start':
                start_services(args, db)
            result = status(args.state, db)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
