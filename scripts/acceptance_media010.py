#!/usr/bin/env python3
"""Real local models on frozen development goals and one complete public document.

No network download or Spark execution. Run in a reserved inference window.
MongoMock isolates the adapter/cache/CAS check; this is not Mongo deployment proof.
"""
import argparse
import gc
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mongomock
from knowpipe.learning import content
from knowpipe.learning.local_providers import LocalTranslator, configured_goal_translator
from knowpipe.learning.providers import processor_identity, translate_document
from knowpipe.recommendations.query import prepare_goal


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--goal-model', default='state/feature010/media/translate-zh_en-1_9')
    parser.add_argument('--reading-model', default='state/feature009/media/translate-en_zh-1_9')
    parser.add_argument('--corpus', default='state/feature009/corpus-expanded/documents.jsonl')
    parser.add_argument('--evidence', default='evidence/010-quality-cluster-readiness')
    args = parser.parse_args()
    evidence = Path(args.evidence)
    db = mongomock.MongoClient().db
    provider = configured_goal_translator({'KNOWPIPE_GOAL_TRANSLATION_MODEL_PATH': args.goal_model})
    manifest = json.loads((evidence / 'goal-cases.json').read_text())
    model_record = json.loads((evidence / 'goal-model.json').read_text())
    goal_record = {'recorded_at': datetime.now(timezone.utc).isoformat(),
                   'purpose': manifest['purpose'], 'database': 'mongomock; cache and adapter isolation only',
                   'model': model_record, 'processor_id': processor_identity(provider), 'cases': []}
    for case in manifest['cases']:
        start = time.monotonic()
        query = prepare_goal(db, case['zh'], provider)
        elapsed = time.monotonic() - start
        cache_same = prepare_goal(db, case['zh'], provider) == query
        goal_record['cases'].append({'id': case['id'], 'case': case, 'query': query, 'seconds': round(elapsed, 6),
                                     'repeat_query_equal': cache_same})
        write(evidence / 'goal-interpretations.json', goal_record)
        print(json.dumps({'id': case['id'], 'status': query['status'], 'seconds': round(elapsed, 3)}, ensure_ascii=False), flush=True)
    del provider
    gc.collect()

    # Fixed before looking at this run: the entire existing PostgreSQL WAL chapter,
    # not a hand-written sentence or an excerpt selected for easy translation.
    doc = None
    with Path(args.corpus).open() as source:
        for line in source:
            candidate = json.loads(line)
            if candidate.get('source') == 'postgresql_docs' and candidate.get('doc_id') == '18/wal-intro.html':
                doc = candidate
                break
    if doc is None:
        raise ValueError('frozen_fulltext_document_missing')
    db.documents.insert_one(doc)
    content.publish_fulltext(db, doc['source'], doc['doc_id'], doc['body_text'], doc['language'])
    delegate = LocalTranslator(args.reading_model)

    class RecordingProvider:
        processor_id = delegate.processor_id
        output = None

        def translate(self, *arguments):
            self.output = delegate.translate(*arguments)
            return self.output

    provider = RecordingProvider()
    start = time.monotonic()
    view = translate_document(db, doc['source'], doc['doc_id'], provider)
    result = {'recorded_at': datetime.now(timezone.utc).isoformat(),
              'purpose': '真实全文与模型的开发验收；非独立翻译质量评价或学习收益证明',
              'source': {key: doc.get(key) for key in ('source', 'doc_id', 'title', 'source_url', 'license', 'provenance')},
              'source_characters': len(doc['body_text']), 'original_text': doc['body_text'],
              'provider_output': provider.output.text if provider.output else None,
              'processor_id': processor_identity(provider), 'seconds': round(time.monotonic() - start, 6),
              'view': view,
              'quality_limitations': ['数字/代码完整性不检测术语、语义关系、一般文字遗漏。',
                                      '不把完整处理或规则通过当作中文教学质量达标。',
                                      '模型未升级，仍为已有 Argos en_zh 1.9 基线。']}
    write(evidence / 'translation-quality.json', result)
    print(json.dumps({'translation_ready': view['chinese_ready'], 'quality': view['translation_quality'],
                      'seconds': result['seconds']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
