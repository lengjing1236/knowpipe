"""Start a persistent local defense demo; optionally attach a temporary HTTPS tunnel.

Run from any directory: python3 scripts/defense_demo.py start [--public]
All private data and logs stay in ignored state/defense/. No cloud account is needed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'state/defense'
MANIFEST = STATE / 'services.json'
MONGO_URI = 'mongodb://127.0.0.1:27029'
DATABASE = 'knowpipe_defense'


def private_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def running(record):
    """Verify Linux process start time so stale PID files cannot stop unrelated work."""
    try:
        fields = Path(f'/proc/{record["pid"]}/stat').read_text().split()
        return fields[2] != 'Z' and fields[21] == record['start_ticks']
    except (OSError, KeyError):
        return False


def stop(manifest):
    for record in reversed(manifest.get('processes', [])):
        if running(record):
            os.killpg(record['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 15
    while any(running(record) for record in manifest.get('processes', [])):
        if time.monotonic() >= deadline:
            raise RuntimeError('服务仍在退出，请稍后运行 status；未删除任何数据库文件。')
        time.sleep(.2)


def free_ports():
    for port in (27029, 8019, 8020):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', port))
            except OSError as exc:
                raise RuntimeError(f'端口 {port} 已被占用，请先检查已有服务。') from exc


def start(public=False):
    if MANIFEST.exists():
        previous = json.loads(MANIFEST.read_text())
        if any(running(record) for record in previous.get('processes', [])):
            status()
            print('已有演示服务运行；如需更改模式，先 stop 再 start。')
            return
    free_ports()
    mongod = os.environ.get('MONGOD_BIN') or shutil.which('mongod')
    if not mongod:
        candidates = sorted((Path.home() / 'mongodb-local').glob('*/bin/mongod'))
        mongod = str(candidates[-1]) if candidates else None
    if not mongod:
        raise RuntimeError('未找到 mongod。安装 MongoDB，或设置 MONGOD_BIN 为可执行文件路径。')
    tunnel_bin = shutil.which('cloudflared') or str(STATE / 'bin/cloudflared')
    if public and not Path(tunnel_bin).is_file():
        raise RuntimeError('未找到 cloudflared；先按答辩运行手册安装，或不加 --public 启动本机演示。')
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    (STATE / 'mongo').mkdir(exist_ok=True)
    key_file = STATE / 'secret-key'
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
        key_file.chmod(0o600)
    env = {**os.environ, 'SECRET_KEY': key_file.read_text().strip(),
           'MONGO_URI': MONGO_URI, 'MONGO_DB': DATABASE,
           'SPARK_LOCAL_IP': '127.0.0.1', 'SPARK_MASTER': 'local[2]',
           'APP_ENV': 'development', 'PUBLIC_ORIGIN': '', 'TRUST_PROXY': '0'}
    manifest = {'local_url': 'http://127.0.0.1:8019', 'public_url': None,
                'mongo_uri': MONGO_URI, 'database': DATABASE, 'processes': []}

    def launch(name, args, overrides=None):
        with (STATE / f'{name}.log').open('a') as log:
            process = subprocess.Popen(args, cwd=ROOT, env={**env, **(overrides or {})},
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)
        record = {'name': name, 'pid': process.pid,
                  'start_ticks': Path(f'/proc/{process.pid}/stat').read_text().split()[21]}
        manifest['processes'].append(record)
        private_json(MANIFEST, manifest)
        return process

    def gunicorn(port):
        return [sys.executable, '-m', 'gunicorn', '--bind', f'127.0.0.1:{port}',
                '--workers', '2', '--worker-class', 'gthread', '--threads', '8',
                '--timeout', '60', '--access-logfile', '-', 'knowpipe.web.app:create_app()']

    try:
        launch('mongo', [mongod, '--bind_ip', '127.0.0.1', '--port', '27029',
                         '--dbpath', str(STATE / 'mongo'), '--wiredTigerCacheSizeGB', '0.25'])
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
        with MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000) as client:
            for attempt in range(20):
                try:
                    client.admin.command('ping')
                    break
                except PyMongoError:
                    if attempt == 19:
                        raise RuntimeError('MongoDB 启动失败，查看 state/defense/mongo.log')
                    time.sleep(.3)
        launch('web-local', gunicorn(8019))
        if public:
            # Empty config prevents unrelated user tunnel credentials/config from being used.
            tunnel_config = STATE / 'tunnel.yml'
            tunnel_config.write_text('{}\n')
            log = STATE / 'tunnel.log'
            offset = log.stat().st_size if log.exists() else 0
            process = launch('tunnel', [tunnel_bin, 'tunnel', '--config', str(tunnel_config),
                '--url', 'http://127.0.0.1:8020', '--protocol', 'http2', '--no-autoupdate'])
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and process.poll() is None:
                with log.open() as stream:
                    stream.seek(offset)
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', stream.read())
                if match:
                    manifest['public_url'] = match.group()
                    break
                time.sleep(.5)
            if not manifest['public_url']:
                raise RuntimeError('临时隧道创建失败，查看 tunnel.log；可去掉 --public 启动本机演示。')
            launch('web-public', gunicorn(8020), {'APP_ENV': 'production', 'TRUST_PROXY': '1',
                                                'PUBLIC_ORIGIN': manifest['public_url']})
        launch('worker', [sys.executable, '-m', 'knowpipe.podcasts.worker'])
        private_json(MANIFEST, manifest)
        import urllib.request
        for attempt in range(30):
            try:
                with urllib.request.urlopen(manifest['local_url'] + '/health/ready', timeout=2) as result:
                    if result.status == 200:
                        break
            except OSError:
                if attempt == 29:
                    raise RuntimeError('Web 启动失败，查看 web-local.log')
                time.sleep(.2)
        status()
    except BaseException:
        stop(manifest)
        raise


def status():
    if not MANIFEST.exists():
        print('尚未启动演示。')
        return
    manifest = json.loads(MANIFEST.read_text())
    print('本机入口：' + manifest['local_url'] + '/login')
    if manifest.get('public_url'):
        print('临时公网入口（以实际 HTTP 验证为准）：' + manifest['public_url'] + '/login')
    for record in manifest['processes']:
        print(record['name'] + ': ' + ('运行中' if running(record) else '已停止'))
    print('数据、私有配置和日志：' + str(STATE))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status'])
    parser.add_argument('--public', action='store_true', help='生成临时 HTTPS 访问地址')
    args = parser.parse_args()
    try:
        if args.command == 'start':
            start(args.public)
        elif args.command == 'stop':
            if MANIFEST.exists():
                stop(json.loads(MANIFEST.read_text()))
            print('演示服务已停止，数据库文件保留。')
        else:
            status()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
