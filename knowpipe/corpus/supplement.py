"""Add bounded official database and container references to an immutable base corpus."""
import argparse
import base64
import hashlib
import json
import shutil
from pathlib import Path

from .html import TextExtractor
from .network import CachedClient, FetchStopped
from .pipeline import CorpusWriter
from .stackexchange import CorpusRejected

PAGES = (
    {'source': 'postgresql_docs', 'base': 'https://www.postgresql.org/docs/18/', 'provider': 'PostgreSQL Global Development Group',
     'license': 'PostgreSQL License', 'license_url': 'https://www.postgresql.org/docs/18/legalnotice.html',
     'domain': '数据库', 'tags': ['postgresql'], 'version': '18', 'paths': (
         'transaction-iso.html', 'explicit-locking.html', 'mvcc-caveats.html', 'mvcc-serialization-failure-handling.html',
         'indexes-types.html', 'indexes-multicolumn.html', 'indexes-ordering.html', 'indexes-unique.html',
         'indexes-expressional.html', 'indexes-partial.html', 'indexes-index-only-scans.html', 'indexes-bitmap-scans.html',
         'indexes-opclass.html', 'indexes-collations.html', 'indexes-examine.html', 'using-explain.html',
         'planner-stats.html', 'sql-explain.html', 'sql-createindex.html', 'ddl-partitioning.html',
         'wal-intro.html', 'wal-configuration.html', 'backup-dump.html', 'continuous-archiving.html')},
    {'source': 'docker_docs', 'base': 'https://docs.docker.com/', 'provider': 'Docker, Inc. and contributors',
     'license': 'Apache-2.0', 'license_url': 'https://github.com/docker/docs/blob/main/LICENSE',
     'license_fetch_url': 'https://api.github.com/repos/docker/docs/contents/LICENSE?ref=main',
     'domain': '系统与网络运维', 'tags': ['docker', 'linux', 'containers'], 'version': 'unversioned', 'paths': (
         'engine/network/', 'engine/network/drivers/bridge/', 'engine/network/drivers/host/',
         'engine/network/drivers/overlay/', 'engine/network/drivers/macvlan/', 'engine/network/drivers/ipvlan/',
         'engine/storage/volumes/', 'engine/storage/bind-mounts/', 'engine/storage/tmpfs/',
         'engine/containers/resource_constraints/', 'engine/logging/configure/', 'engine/security/rootless/')},
)


def supplemental_record(html, source, path, provenance, license_provenance):
    if source['source'] == 'postgresql_docs':
        selector = lambda tag, attrs: tag == 'div' and bool({'sect1', 'refentry'} & set(attrs.get('class', '').split()))
    elif source['source'] == 'docker_docs':
        selector = lambda tag, attrs: tag == 'article' and 'prose' in attrs.get('class', '').split()
    else:
        raise CorpusRejected('unsupported_supplemental_source')
    parser = TextExtractor(source['base'] + path, selector, title_tags=('h1', 'h2'), table_cells=True)
    parser.feed(html)
    text, images = parser.finish()
    title = ' '.join(''.join(parser.title).split())
    if not parser.found or not title or len(text) < 500:
        raise CorpusRejected('official_supplement_main_content_missing')
    return {'source': source['source'], 'doc_id': source['version'] + '/' + path.rstrip('/'),
            'title': title, 'body_text': text, 'language': 'en', 'source_url': source['base'] + path,
            'license': source['license'] + '; ' + source['license_url'], 'fulltext_verified': True,
            'document_type': 'official_document', 'topic_domain': source['domain'], 'tags': source['tags'],
            'authors': [{'name': source['provider'], 'url': source['base'], 'license': source['license'],
                         'license_policy_url': source['license_url']}],
            'quality': {'extraction_method': 'official_html_main_container', 'contains_media': bool(images),
                        'media_urls': images, 'media_content_extracted': False, 'software_version': source['version']},
            'provenance': {**provenance, 'adapter_version': 'supplemental-official-html-v1',
                           'original_provider': source['provider'], 'license_artifact': license_provenance,
                           'modifications': 'Main text extracted from HTML; navigation removed, code retained.'}}


def prepare(base, output):
    base, output = Path(base).resolve(), Path(output).resolve()
    if base == output or base in output.parents or output in base.parents:
        raise ValueError('separate_snapshot_directory_required')
    base_manifest = json.loads((base / 'manifest.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'responses'; cache.mkdir(exist_ok=True)
    # Copy only missing immutable cache files. The base snapshot is never written.
    for source in (base / 'responses').iterdir():
        target = cache / source.name
        if source.is_file() and source.suffix in {'.raw', '.json'} and not target.exists():
            shutil.copy2(source, target)
    client = CachedClient(cache, max_requests=50, transient_retries=2)
    writer = CorpusWriter(output)
    base_digest = hashlib.sha256()
    with (base / 'documents.jsonl').open('rb') as stream:
        for line in stream:
            base_digest.update(line)
            writer.add(json.loads(line))
    if base_digest.hexdigest() != base_manifest['corpus_sha256'] or writer.count != base_manifest['valid_documents']:
        writer.stream.close()
        raise ValueError('base_snapshot_hash_or_count_mismatch')
    initial_count = writer.count
    failures, added = [], []
    try:
        for source in PAGES:
            license_raw, license_provenance = client.bytes(source.get('license_fetch_url', source['license_url']))
            if source.get('license_fetch_url'):
                content = json.loads(license_raw)
                if content.get('path') != 'LICENSE' or content.get('encoding') != 'base64':
                    raise ValueError('unexpected_license_response')
                license_raw = base64.b64decode(''.join(content['content'].split()), validate=True)
                if b'Apache License' not in license_raw or b'Version 2.0' not in license_raw:
                    raise ValueError('unexpected_docker_license')
                license_provenance = {**license_provenance, 'git_blob_sha': content['sha'],
                                      'decoded_sha256': hashlib.sha256(license_raw).hexdigest(), 'encoding': 'github_contents_base64'}
            license_file = output / (source['source'] + ('-LICENSE.txt' if source.get('license_fetch_url') else '-LICENSE.html'))
            license_file.write_bytes(license_raw)
            license_provenance = {**license_provenance, 'local_filename': license_file.name,
                                  'stored_sha256': hashlib.sha256(license_raw).hexdigest()}
            for path in source['paths']:
                try:
                    raw, provenance = client.bytes(source['base'] + path)
                    record = supplemental_record(raw.decode('utf-8'), source, path, provenance, license_provenance)
                    if writer.add(record):
                        added.append({k: record[k] for k in ('source', 'doc_id', 'title', 'source_url')})
                    print(json.dumps({'source': source['source'], 'path': path, 'supplement_documents': writer.count - initial_count}, ensure_ascii=False), flush=True)
                except CorpusRejected as error:
                    writer.rejected[str(error)] += 1
    except (FetchStopped, OSError, ValueError) as error:
        failures.append(str(error))
    manifest = writer.finish(base_snapshot={'path': str(base), 'corpus_sha256': base_manifest['corpus_sha256'],
                                            'documents': base_manifest['valid_documents']},
                             supplement_documents=writer.count - initial_count, supplement_sources=added,
                             expected_supplement_documents=36, target_met=writer.count - initial_count == 36,
                             network_requests=client.requests, cache_hits=client.cache_hits, failures=failures)
    print(json.dumps({k: manifest[k] for k in ('valid_documents', 'supplement_documents', 'target_met', 'failures', 'corpus_sha256')}, ensure_ascii=False), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='state/feature009/corpus')
    parser.add_argument('--output', default='state/feature009/corpus-expanded')
    args = parser.parse_args()
    result = prepare(args.base, args.output)
    raise SystemExit(0 if result['target_met'] and not result['failures'] else 2)


if __name__ == '__main__':
    main()
