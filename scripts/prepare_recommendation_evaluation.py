#!/usr/bin/env python3
"""Download only the fixed ~20 MB external evaluation slice, with hash checks."""
import argparse
import json
import os
from pathlib import Path

import requests

from knowpipe.recommendations.evaluation import FILES, file_hash, verify_files


def main():
    parser = argparse.ArgumentParser(description='准备固定 CQADupStack programmers 外部检索评价资料')
    parser.add_argument('--output', default='state/feature009/evaluation')
    parser.add_argument('--endpoint', choices=['https://huggingface.co', 'https://hf-mirror.com'],
                        default='https://huggingface.co')
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    for name, (repo, revision, relative, expected) in FILES.items():
        target = directory / name
        if target.exists() and file_hash(target) == expected:
            continue
        temporary = target.with_suffix(target.suffix + '.part')
        try:
            url = f'{args.endpoint}/datasets/{repo}/resolve/{revision}/{relative}'
            with requests.get(url, stream=True, timeout=(8, 30)) as response:
                response.raise_for_status()
                count = 0
                with temporary.open('wb') as stream:
                    for chunk in response.iter_content(65536):
                        count += len(chunk)
                        if count > 30_000_000:
                            raise ValueError('evaluation_file_over_30_MB')
                        stream.write(chunk)
            if file_hash(temporary) != expected:
                raise ValueError(f'evaluation_file_hash_mismatch:{name}')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    report = {'download_endpoint': args.endpoint, 'files': verify_files(directory),
              'purpose': '外部重复问题检索评价；不是完整问答生产语料，也不是新的原始来源。'}
    (directory / 'download-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
