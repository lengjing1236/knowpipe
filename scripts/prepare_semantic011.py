#!/usr/bin/env python3
"""Acquire a pinned, bounded CPU semantic bundle; never run model inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import requests


MODELS = {
    'rank': ('cross-encoder/ms-marco-MiniLM-L6-v2', '233902d25c440f23af6f7d6e94d2946bac0bee0a',
             'c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9', 23200716),
    'embed': ('sentence-transformers/all-MiniLM-L6-v2', '1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
              'b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21', 23046789),
    'nli': ('cross-encoder/nli-MiniLM2-L6-H768', 'b95119ce93d3e065de6214e38cd4a97b0f2f2c6d',
            '44391a5241a62e0083c1a8899a71e69a092b95aea5ba89e14062925468eceac7', 82823063),
}
FILES = ('onnx/model_quint8_avx2.onnx', 'tokenizer.json', 'config.json', 'tokenizer_config.json')
MAX_TOTAL = 180_000_000
MULTILINGUAL = {
    'rank': ('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', '1427fd652930e4ba29e8149678df786c240d8825',
             '6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b', 118620016),
    'embed': ('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42',
              '98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f', 118453870),
    'nli': MODELS['nli'],
}


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, path, maximum, expected=None):
    path = Path(path)
    if path.is_file() and path.stat().st_size <= maximum and (not expected or checksum(path) == expected):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    try:
        with requests.get(url, stream=True, timeout=(10, 45)) as response:
            response.raise_for_status()
            if int(response.headers.get('Content-Length', 0)) > maximum:
                raise ValueError('model_download_too_large')
            count = 0
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(1 << 18):
                    count += len(chunk)
                    if count > maximum:
                        raise ValueError('model_download_too_large')
                    stream.write(chunk)
        if expected and checksum(temporary) != expected:
            raise ValueError('model_checksum_mismatch')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='state/feature011/semantic')
    parser.add_argument('--endpoint', choices=['https://huggingface.co', 'https://hf-mirror.com'],
                        default='https://huggingface.co')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--multilingual', action='store_true')
    args = parser.parse_args()
    root = Path(args.root)
    manifest = {'format': 'knowpipe-semantic-onnx-v1', 'language': 'multilingual' if args.multilingual else 'en', 'models': {},
                'download_endpoint': args.endpoint, 'inference_performed': False}
    total = 0
    for name, (repository, revision, digest, size) in (MULTILINGUAL if args.multilingual else MODELS).items():
        files = {}
        for filename in FILES:
            local = 'model.onnx' if filename.startswith('onnx/') else filename
            destination = root / name / local
            maximum = size if local == 'model.onnx' else (20_000_000 if args.multilingual else 2_000_000)
            if args.download:
                fetch(f'{args.endpoint}/{repository}/resolve/{revision}/{filename}', destination,
                      maximum, digest if local == 'model.onnx' else None)
            if not destination.is_file():
                parser.error('Model absent; use --download to acquire the pinned models explicitly.')
            measured = destination.stat().st_size
            if measured > maximum or (local == 'model.onnx' and checksum(destination) != digest):
                raise ValueError('invalid_model_file')
            total += measured
            if total > (400_000_000 if args.multilingual else MAX_TOTAL):
                raise ValueError('model_bundle_too_large')
            files[local] = {'bytes': measured, 'sha256': checksum(destination)}
        manifest['models'][name] = {'repository': repository, 'revision': revision,
            'source': f'https://huggingface.co/{repository}/tree/{revision}', 'license': 'Apache-2.0', 'files': files}
        print(json.dumps({'prepared': name, 'total_bytes': total}), flush=True)
    manifest['total_bytes'] = total
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
