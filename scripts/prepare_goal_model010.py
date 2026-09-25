#!/usr/bin/env python3
"""Explicit bounded acquisition of the free official Argos Chinese→English model."""
import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.acceptance_media import digest, fetch
from knowpipe.learning.providers import processor_identity
from knowpipe.learning.local_providers import LocalTranslator

URL = 'https://argos-net.com/v1/translate-zh_en-1_9.argosmodel'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', default='state/feature010/media')
    parser.add_argument('--evidence', default='evidence/010-quality-cluster-readiness/goal-model.json')
    parser.add_argument('--download', action='store_true', help='Explicitly permit the bounded model download')
    args = parser.parse_args()
    cache, evidence = Path(args.cache), Path(args.evidence)
    archive = cache / 'translate-zh_en-1_9.argosmodel'
    previous = json.loads(evidence.read_text()) if evidence.exists() else {}
    expected = previous.get('archive_sha256') if previous.get('source') == URL else None
    if args.download:
        fetch(URL, archive, 160_000_000, expected_hash=expected)
    if not archive.is_file():
        parser.error('Model absent; use --download to acquire the official model explicitly.')
    checksum = digest(archive)
    if expected and checksum != expected:
        raise ValueError('model_checksum_mismatch')
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 1000 or sum(item.file_size for item in infos) > 512_000_000:
            raise ValueError('invalid_model_archive')
        for item in infos:
            destination = (cache / item.filename).resolve()
            if cache.resolve() not in destination.parents or item.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError('invalid_model_archive')
        package.extractall(cache)
    model = cache / 'translate-zh_en-1_9'
    metadata = json.loads((model / 'metadata.json').read_text())
    if metadata.get('from_code') != 'zh' or metadata.get('to_code') != 'en':
        raise ValueError('model_direction_mismatch')
    provider = LocalTranslator(model, source_language='zh', target_language='en')
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'source': URL,
              'official_catalog': 'https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json',
              'package_version': metadata.get('package_version'), 'archive_bytes': archive.stat().st_size,
              'archive_sha256': checksum, 'processor_id': processor_identity(provider),
              'model_path_relative': str(model), 'first_download_hash_is_observation': True,
              'inference_performed': False}
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
