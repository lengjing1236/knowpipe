#!/usr/bin/env python3
"""Real language development comparison. Held-out cases are deliberately excluded."""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.learning.local_providers import NllbTranslator
from knowpipe.learning.providers import processor_identity
from knowpipe.learning.quality import check_translation, validate_segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='state/feature011/media/nllb-200-distilled-600M-ct2-int8')
    parser.add_argument('--evidence', default='evidence/011-mvp-recommendation-validation')
    args = parser.parse_args()
    root = Path(args.evidence)
    old = json.loads(Path('evidence/010-quality-cluster-readiness/goal-interpretations.json').read_text())
    wal = json.loads(Path('evidence/010-quality-cluster-readiness/translation-quality.json').read_text())
    manifest = json.loads((root / 'cases.json').read_text())
    provider = NllbTranslator(args.model, threads=2)
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(),
              'purpose': '真实开发A/B；不使用保留场景调参，不是独立人工评价',
              'model_processor': processor_identity(provider), 'cases': [], 'semantic_pass': None}

    def save():
        result['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        (root / 'language-quality.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    cases = [{'id': 'old-' + item['id'], 'input': item['case']['zh'], 'reference': item['case']['en'],
              'argos': item['query']['variants'][-1]} for item in old['cases']]
    seen = {case['input'] for case in cases}
    for case in manifest['cases']:
        if case.get('split') in ('development', 'dev') and case['goal_zh'] not in seen:
            seen.add(case['goal_zh'])
            cases.append({'id': 'development-' + case['id'], 'input': case['goal_zh'], 'reference': case['goal_en']})
    for item in cases:
        started = time.monotonic()
        try:
            output = provider.translate(item['input'], 'zh', 'en')
            item.update(nllb=output.text, alignment_valid=bool(validate_segments(item['input'], output.text, output.segments)))
        except Exception as error:
            item['error'] = type(error).__name__ + ':' + str(error)
        item['seconds'] = round(time.monotonic() - started, 3)
        result['cases'].append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)
    started = time.monotonic()
    output = provider.translate(wal['original_text'], 'en', 'zh')
    result['fulltext'] = {'source': wal['source'], 'original': wal['original_text'],
        'argos': wal['provider_output'], 'nllb': output.text,
        'integrity': check_translation(wal['original_text'], output.text),
        'segments': validate_segments(wal['original_text'], output.text, output.segments),
        'seconds': round(time.monotonic() - started, 3)}
    save()
    print(json.dumps({'fulltext_integrity': result['fulltext']['integrity'],
                      'seconds': result['fulltext']['seconds'], 'peak_rss_kib': result['peak_rss_kib']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
