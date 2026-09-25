#!/usr/bin/env python3
"""Development-only model-feature diagnostics, never independent MVP labels."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowpipe.recommendations.semantic import LocalSemantic

CASES = [
    {'id': 'hash-collisions', 'zh': '哈希表如何处理不同键的哈希冲突？',
     'en': 'How does a hash table handle collisions between different keys?',
     'passages': [
         {'label': 'direct', 'text': 'A hash table can resolve collisions using separate chaining: each bucket stores multiple key-value entries, and lookup compares the original keys inside that bucket.'},
         {'label': 'generic_distractor', 'text': 'A database table contains rows and columns. Primary keys identify rows, while SQL statements can insert and delete records from the table.'},
         {'label': 'unrelated', 'text': 'Garden flowers require sunlight, soil, water and suitable temperatures. Gardeners regularly remove weeds and inspect the leaves for insect damage.'}]},
    {'id': 'tcp-retransmission', 'zh': 'TCP如何通过重传处理网络数据丢失？',
     'en': 'How does TCP use retransmission to handle lost network data?',
     'passages': [
         {'label': 'direct', 'text': 'TCP keeps transmitted data until it has been acknowledged. When the retransmission timer expires without an acknowledgement, the sender retransmits the outstanding data.'},
         {'label': 'generic_distractor', 'text': 'A network administrator can configure interface addresses and routing tables. The routing table selects the next hop used to forward outgoing packets.'},
         {'label': 'unrelated', 'text': 'Image classification assigns a label to an input picture. A convolutional network learns visual patterns using labelled training images.'}]},
    {'id': 'binary-search', 'zh': '为什么二分查找要求数组预先有序？',
     'en': 'Why does binary search require an array to be sorted first?',
     'passages': [
         {'label': 'direct', 'text': 'Binary search compares the target with the middle array element and eliminates one half of the search interval. The array must be sorted so that this comparison identifies the half that can contain the target.'},
         {'label': 'generic_distractor', 'text': 'An array stores elements at consecutive positions. An index selects a particular element, and some programming languages automatically check whether that index lies inside the array bounds.'},
         {'label': 'unrelated', 'text': 'A database transaction groups several changes into a single operation. Applications can commit the transaction or roll it back when an error occurs.'}]},
    {'id': 'deadlock', 'zh': '两个事务互相等待对方持有的锁为什么会死锁？',
     'en': 'Why do two transactions deadlock when each waits for a lock held by the other?',
     'passages': [
         {'label': 'direct', 'text': 'A deadlock occurs when two transactions each hold a lock needed by the other. Neither transaction can continue, so the database must detect the cycle and abort one transaction to release its locks.'},
         {'label': 'generic_distractor', 'text': 'Database transactions can insert customer records and update account balances. A transaction log records committed changes and helps restore data after a server failure.'},
         {'label': 'unrelated', 'text': 'A hash table can store multiple entries in a bucket. Lookup compares keys in the bucket to locate the entry requested by the caller.'}]},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='state/feature011/semantic-multilingual')
    parser.add_argument('--output', default='evidence/011-mvp-recommendation-validation/semantic-calibration.json')
    parser.add_argument('--freeze-only', action='store_true')
    args = parser.parse_args()
    path = Path(args.output)
    payload = {'purpose': 'developer-authored diagnostic fixtures; not independent human labels or MVP acceptance',
               'cases': CASES}
    frozen = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    fixture_path = path.with_name('semantic-calibration-inputs.json')
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    if fixture_path.exists() and fixture_path.read_bytes() != frozen:
        raise ValueError('calibration_inputs_changed')
    fixture_path.write_bytes(frozen)
    if args.freeze_only:
        print(hashlib.sha256(frozen).hexdigest())
        return
    model = LocalSemantic(args.model, threads=2, batch_size=4)
    started = time.monotonic()
    results = []
    for case in CASES:
        texts = [p['text'] for p in case['passages']]
        native = model.relevance(case['zh'], texts)
        reference = model.relevance(case['en'], texts)
        vectors = model.encode([case['zh'], case['en'], *texts])
        rows = []
        for index, passage in enumerate(case['passages']):
            rows.append({**passage, 'native_rank': native[index], 'reference_rank': reference[index],
                'native_cosine': sum(x * y for x, y in zip(vectors[0], vectors[index + 2])),
                'reference_cosine': sum(x * y for x, y in zip(vectors[1], vectors[index + 2]))})
        results.append({'id': case['id'], 'zh': case['zh'], 'en': case['en'], 'passages': rows})
    result = {'input_sha256': hashlib.sha256(frozen).hexdigest(), 'processor_id': model.processor_id,
              'seconds': time.monotonic() - started, 'cases': results, 'is_mvp_acceptance': False}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    model.close()


if __name__ == '__main__':
    main()
