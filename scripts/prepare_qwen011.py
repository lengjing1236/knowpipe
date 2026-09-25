#!/usr/bin/env python3
"""Prepare pinned Qwen GGUF and official llama.cpp CPU binary; never starts inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'
REVISION = '91cad51170dc346986eccefdc2dd33a9da36ead9'
MODEL = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
MODEL_SIZE = 1117320736
MODEL_SHA = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'
LLAMA_COMMIT = '4762ad7316dcdec20016ab5985fb46a27902204d'
LLAMA_URL = 'https://github.com/ggml-org/llama.cpp/releases/download/b6000/llama-b6000-bin-ubuntu-x64.zip'
LLAMA_API_URL = 'https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/276767743'
LLAMA_SIZE = 13113111
LLAMA_SHA = '66244356dd242ae3a2510929ec4a840db65a3f9e7ad712918dbf02a266badfa5'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def fetch(url, path, size, checksum, *, headers=None):
    if path.exists() and path.stat().st_size == size and digest(path) == checksum:
        return
    partial = path.with_suffix(path.suffix + '.part')
    started = time.monotonic()
    print('Downloading ' + path.name, flush=True)
    for attempt in range(3):
        count = partial.stat().st_size if partial.exists() else 0
        if count > size:
            raise ValueError('download_limit')
        if count == size:
            break
        options = {'User-Agent': 'Knowpipe explicit model preparation/1.0', **(headers or {})}
        if count:
            options['Range'] = f'bytes={count}-'
        request = urllib.request.Request(url, headers=options)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status == 206:
                    if not response.headers.get('Content-Range', '').startswith(f'bytes {count}-'):
                        raise ValueError('download_range_mismatch')
                    mode = 'ab'
                elif response.status == 200:
                    mode, count = 'wb', 0
                else:
                    raise ValueError('download_status')
                next_report = count
                with partial.open(mode) as stream:
                    for chunk in iter(lambda: response.read(65536), b''):
                        count += len(chunk)
                        if count > size or time.monotonic() - started > 1800:
                            raise ValueError('download_limit')
                        stream.write(chunk)
                        if count >= next_report:
                            print(f'{path.name}: {count}/{size} bytes', flush=True)
                            next_report = count + 128 * 1024 * 1024
            if count == size:
                break
        except (OSError, socket.timeout):
            if attempt == 2:
                raise
            print(f'Resuming after network interruption: attempt {attempt + 2}', flush=True)
    if partial.stat().st_size != size or digest(partial) != checksum:
        raise ValueError('download_integrity')
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', default='state/feature011/media')
    parser.add_argument('--evidence', default='evidence/011-mvp-recommendation-validation/qwen-model.json')
    parser.add_argument('--mirror', choices=['https://huggingface.co', 'https://hf-mirror.com'], default='https://huggingface.co')
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    root, evidence = Path(args.cache), Path(args.evidence)
    root.mkdir(parents=True, exist_ok=True)
    model, archive = root / MODEL, root / 'llama-b6000-bin-ubuntu-x64.zip'
    url = f'{args.mirror}/{REPO}/resolve/{REVISION}/{MODEL}'
    for source, path, size, checksum in [(url, model, MODEL_SIZE, MODEL_SHA),
                                       (LLAMA_API_URL, archive, LLAMA_SIZE, LLAMA_SHA)]:
        if args.download:
            fetch(source, path, size, checksum,
                  headers={'Accept': 'application/octet-stream'} if source == LLAMA_API_URL else None)
        if not path.is_file() or path.stat().st_size != size or digest(path) != checksum:
            parser.error('Pinned file missing or invalid; --download permits bounded acquisition.')
    binary_root = root / 'llama-b6000'
    binary_root.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 1000 or sum(info.file_size for info in infos) > 300_000_000:
            raise ValueError('binary_archive_limit')
        for info in infos:
            target = (binary_root / info.filename).resolve()
            if binary_root.resolve() not in target.parents or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('invalid_binary_archive')
        package.extractall(binary_root)
        for info in infos:
            target = binary_root / info.filename
            if target.is_file() and (info.external_attr >> 16) & 0o111:
                target.chmod(0o755)
    servers = list(binary_root.rglob('llama-server'))
    if len(servers) != 1:
        raise ValueError('missing_llama_server')
    server = servers[0]
    server.chmod(0o755)
    record = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'model_repository': REPO,
              'model_revision': REVISION, 'model_url': url,
              'official_model_url': f'https://huggingface.co/{REPO}/resolve/{REVISION}/{MODEL}',
              'model_sha256': MODEL_SHA, 'model_bytes': MODEL_SIZE, 'model_license': 'Apache-2.0',
              'transport_is_mirror': args.mirror != 'https://huggingface.co',
              'hash_source': 'Git-LFS pointer of pinned official repository revision, obtained via the recorded mirror',
              'llama_url': LLAMA_URL, 'llama_download_url': LLAMA_API_URL,
              'llama_commit': LLAMA_COMMIT, 'llama_tag': 'b6000',
              'llama_sha256': LLAMA_SHA, 'llama_bytes': LLAMA_SIZE, 'llama_license': 'MIT',
              'llama_hash_source': 'Official GitHub release asset digest',
              'model_path': str(model.absolute()), 'server_path': str(server.absolute()),
              'inference_performed': False, 'compatibility_checked': False}
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
