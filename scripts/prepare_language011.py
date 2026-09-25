#!/usr/bin/env python3
"""Explicit, bounded acquisition of the NLLB CPU translation candidate; no inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://pretrained-nmt-models.s3.us-west-004.backblazeb2.com/CTranslate2/nllb/'
FILES = {'nllb-200_600M_int8_ct2.zip': 578586670,
         'flores200_sacrebleu_tokenizer_spm.model': 4852054}
MAX_DOWNLOAD = 650_000_000
MAX_EXPANDED = 1_000_000_000


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def fetch(name, cache, expected_hash=None):
    path, size = cache / name, FILES[name]
    if path.is_file() and path.stat().st_size == size:
        if not expected_hash or digest(path) == expected_hash:
            return path
    part = path.with_suffix(path.suffix + '.part')
    request = urllib.request.Request(BASE + name, headers={'User-Agent': 'Knowpipe research model preparation/1.0'})
    print('Downloading ' + name, flush=True)
    try:
        with urllib.request.urlopen(request, timeout=45) as response, part.open('wb') as stream:
            if response.status != 200:
                raise ValueError('unexpected_download_status')
            count = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                count += len(chunk)
                if count > size:
                    raise ValueError('model_download_limit')
                stream.write(chunk)
        if count != size or (expected_hash and digest(part) != expected_hash):
            raise ValueError('model_download_integrity')
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', default='state/feature011/media')
    parser.add_argument('--evidence', default='evidence/011-mvp-recommendation-validation/language-model.json')
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    cache, evidence = Path(args.cache), Path(args.evidence)
    cache.mkdir(parents=True, exist_ok=True)
    if sum(FILES.values()) > MAX_DOWNLOAD:
        raise ValueError('model_download_limit')
    previous = json.loads(evidence.read_text()) if evidence.is_file() else {}
    checksums = {item['name']: item['sha256'] for item in previous.get('files', []) if item.get('url') == BASE + item.get('name', '')}
    paths = {}
    for name, size in FILES.items():
        path = fetch(name, cache, checksums.get(name)) if args.download else cache / name
        if not path.is_file() or path.stat().st_size != size:
            parser.error('Model missing; --download explicitly enables bounded acquisition.')
        if checksums.get(name) and digest(path) != checksums[name]:
            raise ValueError('model_checksum_mismatch')
        paths[name] = path
    target = cache / 'nllb-200-distilled-600M-ct2-int8'
    archive = paths['nllb-200_600M_int8_ct2.zip']
    with tempfile.TemporaryDirectory(dir=cache, prefix='nllb-extract-') as directory:
        root = Path(directory).resolve()
        with zipfile.ZipFile(archive) as package:
            infos = package.infolist()
            if (len(infos) > 100 or sum(info.file_size for info in infos)
                    + FILES['flores200_sacrebleu_tokenizer_spm.model'] > MAX_EXPANDED):
                raise ValueError('model_archive_limit')
            for info in infos:
                destination = (root / info.filename).resolve()
                if root not in destination.parents or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('invalid_model_archive_path')
            package.extractall(root)
        models = list(root.rglob('model.bin'))
        if len(models) != 1:
            raise ValueError('invalid_model_archive_contents')
        model = models[0].parent
        if not (model / 'config.json').is_file():
            raise ValueError('invalid_model_archive_contents')
        shutil.copyfile(paths['flores200_sacrebleu_tokenizer_spm.model'], model / 'sentencepiece.bpe.model')
        metadata = {'model': 'facebook/nllb-200-distilled-600M', 'format': 'ctranslate2',
                    'quantization': 'int8', 'license': 'CC-BY-NC-4.0',
                    'source': 'https://forum.opennmt.net/t/nllb-200-with-ctranslate2/5090',
                    'model_card': 'https://huggingface.co/facebook/nllb-200-distilled-600M',
                    'limitations': 'General-domain sentence translation research model; technical document quality requires evaluation.'}
        (model / 'knowpipe-model.json').write_text(json.dumps(metadata, indent=2) + '\n')
        if target.exists():
            # Re-preparation cannot overwrite changed weights silently.
            for file in model.iterdir():
                existing = target / file.name
                if file.is_file() and (not existing.is_file() or digest(existing) != digest(file)):
                    raise ValueError('existing_model_mismatch')
        else:
            shutil.move(str(model), target)
    result = {**metadata, 'recorded_at': datetime.now(timezone.utc).isoformat(),
              'files': [{'name': name, 'url': BASE + name, 'bytes': path.stat().st_size,
                         'sha256': digest(path)} for name, path in paths.items()],
              'model_path': str(target), 'expanded_bytes': sum(p.stat().st_size for p in target.rglob('*') if p.is_file()),
              'first_download_hash_is_observation': not bool(previous), 'inference_performed': False}
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
