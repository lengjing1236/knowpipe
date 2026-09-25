"""Prepare a resumable multi-source corpus using complete official API responses.

No credentials, paid services, pagination bypasses or silent text truncation.
Run from the repository root: python3 scripts/prepare_fulltext_corpus.py
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowpipe.corpus.network import CachedClient, FetchStopped
from knowpipe.corpus.official import OFFICIAL_PAGES, official_record
from knowpipe.corpus.pipeline import CorpusWriter
from knowpipe.corpus.stackexchange import DOMAINS, CorpusRejected, page_url, question_record


def seed_official_cache(cache, sample):
    """Reuse already audited official response bytes, verifying their published digest."""
    manifest = sample / 'manifest.json'
    if not manifest.exists():
        return
    data = json.loads(manifest.read_text())
    for record in data.get('documents', []):
        url = record['source_url']
        old = sample / (record['source'] + '-' + hashlib.sha256(url.encode()).hexdigest()[:12] + '.html')
        if not old.exists():
            continue
        raw = old.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
        if digest != record.get('html_sha256'):
            raise ValueError('official_sample_cache_integrity_error')
        name = hashlib.sha256(url.encode()).hexdigest()
        target = cache / (name + '.raw')
        if target.exists():
            continue
        target.write_bytes(raw)
        (cache / (name + '.json')).write_text(json.dumps({'fetch_url': url, 'retrieved_at': data['fetched_at'],
            'raw_sha256': digest, 'raw_bytes': len(raw), 'reused_from': 'feature008-official-source-sample'}))


def prepare(output, *, target=10000, max_requests=180, sites=tuple(DOMAINS), sample=Path('state/feature008/sample')):
    output = Path(output); cache = output / 'responses'; cache.mkdir(parents=True, exist_ok=True)
    seed_official_cache(cache, Path(sample))
    client = CachedClient(cache, max_requests=max_requests)
    writer = CorpusWriter(output)
    failures, pages = [], {}
    try:
        for source, base, license_name, license_url, paths in OFFICIAL_PAGES:
            for path in paths:
                try:
                    raw, provenance = client.bytes(base + path)
                    writer.add(official_record(raw.decode('utf-8'), source, path, base, license_name, license_url, provenance))
                except CorpusRejected as error:
                    writer.rejected[str(error)] += 1
        exhausted = set()
        for page in range(1, 26):
            for site in sites:
                if writer.count >= target:
                    break
                if site in exhausted:
                    continue
                data, provenance = client.json(page_url(site, page))
                items = data.get('items', [])
                for item in items:
                    try:
                        writer.add(question_record(item, site, provenance))
                    except (CorpusRejected, ValueError) as error:
                        writer.rejected[str(error)] += 1
                pages[site] = page
                if not data.get('has_more') or not items:
                    exhausted.add(site)
                print(json.dumps({'site': site, 'page': page, 'valid_documents': writer.count,
                                  'quota_remaining': client.state.get('se_quota_remaining'), 'network_requests': client.requests}, ensure_ascii=False), flush=True)
            if writer.count >= target:
                break
    except (FetchStopped, OSError, ValueError) as error:
        failures.append(str(error))
    report = writer.finish(target_documents=target, target_met=writer.count >= target,
                           network_requests=client.requests, cache_hits=client.cache_hits,
                           pages=pages, failures=failures, quota_remaining=client.state.get('se_quota_remaining'),
                           query_order='creation descending', source_collection_order=list(sites))
    print(json.dumps({k: report[k] for k in ('valid_documents', 'target_met', 'original_providers', 'failures', 'corpus_sha256')}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='state/feature009/corpus')
    parser.add_argument('--target', type=int, default=10000)
    parser.add_argument('--max-requests', type=int, default=180)
    parser.add_argument('--sites', nargs='+', choices=tuple(DOMAINS), default=list(DOMAINS))
    parser.add_argument('--reuse-sample', default='state/feature008/sample')
    args = parser.parse_args()
    if not 1 <= args.target <= 50000 or not 1 <= args.max_requests <= 250 or len(set(args.sites)) != len(args.sites):
        parser.error('target must be 1..50000; request budget 1..250; distinct sites required')
    result = prepare(args.output, target=args.target, max_requests=args.max_requests, sites=tuple(args.sites), sample=Path(args.reuse_sample))
    raise SystemExit(0 if result['target_met'] and len(result['original_providers']) >= 2 and not result['failures'] else 2)


if __name__ == '__main__':
    main()
