"""Run four real free remote translations without printing or persisting API keys.

This records outputs for source-based review; it does not ask the translator to
grade itself and never converts integrity checks into semantic quality approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowpipe.learning.providers import _translation_error_code
from knowpipe.learning.quality import check_translation, validate_segments
from knowpipe.learning.remote_translation import BigModelTranslator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', default='state/demo012/bigmodel-api-key')
    parser.add_argument('--cases', default='evidence/012-demo-delivery/remote-translation-cases.json')
    parser.add_argument('--output', default='evidence/012-demo-delivery/remote-translation.json')
    args = parser.parse_args()
    key_file, cases_path, output = (ROOT / value for value in (args.key_file, args.cases, args.output))
    if output.exists():
        existing = json.loads(output.read_text(encoding='utf-8'))
        if existing.get('cases'):
            raise SystemExit('existing_actual_result_requires_a_new_output_path')
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'status': 'not_configured',
              'provider': 'BigModel', 'model': BigModelTranslator.MODEL,
              'remote_calls_attempted': 0, 'mocked_responses': False,
              'scope': '公开技术完整段落小样本及中文目标；不代表完整全文、真实网页或一般翻译质量通过',
              'review_status': 'not_reviewed', 'cases': []}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    try:
        key = key_file.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        key = ''
    if not key:
        save()
        print(json.dumps({'status': result['status'], 'remote_calls_attempted': 0}))
        return 2
    if len(key) > 4096 or '\n' in key or '\r' in key:
        raise SystemExit('invalid_key_file_format')
    provider = BigModelTranslator(key, timeout_seconds=180)
    result['processor_id'] = provider.processor_id
    case_bytes = cases_path.read_bytes()
    result['cases_sha256'] = hashlib.sha256(case_bytes).hexdigest()
    result['adapter_sha256'] = hashlib.sha256((ROOT / 'knowpipe/learning/remote_translation.py').read_bytes()).hexdigest()
    for case in json.loads(case_bytes)['cases']:
        record = dict(case)
        record['review_status'] = 'not_reviewed'
        start = time.monotonic()
        result['remote_calls_attempted'] += 1
        try:
            translated = provider.translate(case['input'], case['language'], case['target_language'])
            segments = validate_segments(case['input'], translated.text, translated.segments)
            record.update({'status': 'returned_complete', 'output': translated.text,
                           'segments': segments, 'integrity': check_translation(case['input'], translated.text),
                           'semantic_verified': False})
        except Exception as error:
            record.update({'status': 'failed', 'error_type': type(error).__name__,
                           'error_code': _translation_error_code(error, 'translation_failed')})
        record['seconds'] = round(time.monotonic() - start, 3)
        result['cases'].append(record)
        result['status'] = 'running'
        save()
        print(json.dumps({'case': case['id'], 'status': record['status'], 'seconds': record['seconds']}), flush=True)
    result['status'] = ('remote_outputs_recorded_awaiting_review' if all(
        case['status'] == 'returned_complete' for case in result['cases']) else 'remote_call_failed')
    save()
    return 0 if result['status'] == 'remote_outputs_recorded_awaiting_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())
