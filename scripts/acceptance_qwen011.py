#!/usr/bin/env python3
"""Run the same language development cases through an explicitly started local server."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.learning.llama_provider import LlamaTranslator
from knowpipe.learning.providers import processor_identity
from knowpipe.learning.quality import check_translation, validate_segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='state/feature011/media/qwen2.5-1.5b-instruct-q4_k_m.gguf')
    parser.add_argument('--endpoint', default='http://127.0.0.1:8089')
    parser.add_argument('--smoke', action='store_true', help='Three previously failed technical goals only; no full-text quality claim.')
    parser.add_argument('--output', help='Evidence filename; use a separate file for each changed adapter.')
    args = parser.parse_args()
    root = Path('evidence/011-mvp-recommendation-validation')
    baseline = json.loads((root / 'language-quality.json').read_text())
    provider = LlamaTranslator(args.model, args.endpoint)
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'model_processor': processor_identity(provider),
              'adapter_version': provider.ADAPTER_VERSION,
              'purpose': '真实开发A/B；固定通用翻译指令，无验收句词表或保留场景调参',
              'prompt': provider.SYSTEM, 'cases': [], 'semantic_pass': None}
    output = root / (args.output or ('qwen-smoke.json' if args.smoke else 'qwen-quality.json'))
    prior_path = root / 'qwen-quality.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() and output != prior_path else {}
    prior_cases = {item['id']: item for item in prior.get('cases', [])}

    def save():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    smoke_ids = {'old-lang-1', 'old-lang-2', 'old-web-1'}
    cases = [case for case in baseline['cases'] if case['id'] in smoke_ids] if args.smoke else baseline['cases']
    for old in cases:
        item = {key: old[key] for key in ('id', 'input', 'reference', 'argos', 'nllb') if key in old}
        if old['id'] in prior_cases:
            item['qwen_paragraph'] = prior_cases[old['id']].get('qwen')
        started = time.monotonic()
        try:
            translated = provider.translate(item['input'], 'zh', 'en')
            item['qwen'] = translated.text
            item['alignment_valid'] = bool(validate_segments(item['input'], translated.text, translated.segments))
        except Exception as error:
            item['error'] = type(error).__name__ + ':' + str(error)
        item['seconds'] = round(time.monotonic() - started, 3)
        result['cases'].append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)
    if not args.smoke:
        old = baseline['fulltext']
        started = time.monotonic()
        translated = provider.translate(old['original'], 'en', 'zh')
        result['fulltext'] = {key: old[key] for key in ('source', 'original', 'argos', 'nllb')}
        if prior.get('fulltext'):
            result['fulltext']['qwen_paragraph'] = prior['fulltext'].get('qwen')
        result['fulltext'].update(qwen=translated.text,
            integrity=check_translation(old['original'], translated.text),
            segments=validate_segments(old['original'], translated.text, translated.segments),
            seconds=round(time.monotonic() - started, 3))
        save()
        print(json.dumps({'fulltext_seconds': result['fulltext']['seconds'],
                          'integrity': result['fulltext']['integrity']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
