"""Stream validated independent documents and produce reproducible source counts."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..recommendations.importer import validate_record


class CorpusWriter:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.temporary = self.root / 'documents.jsonl.partial'
        self.stream = self.temporary.open('w', encoding='utf-8')
        self.identities, self.bodies = set(), set()
        self.sources, self.sites, self.domains, self.providers, self.types, self.tags = (Counter() for _ in range(6))
        self.languages, self.missing_post_licenses = Counter(), Counter()
        self.creation_ranges = {}
        self.duplicates, self.rejected = Counter(), Counter()
        self.characters = self.bytes_count = self.paragraph_blocks = self.answers = self.media_docs = 0
        self.max_document_bytes = 0
        self.digest = hashlib.sha256()

    def add(self, record):
        validate_record(record)
        identity = record['source'], record['doc_id']
        body_hash = hashlib.sha256(record['body_text'].replace('\r\n', '\n').encode()).hexdigest()
        if identity in self.identities:
            self.duplicates['identity'] += 1; return False
        if body_hash in self.bodies:
            self.duplicates['body'] += 1; return False
        self.identities.add(identity); self.bodies.add(body_hash)
        encoded = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        self.stream.write(encoded.decode('utf-8')); self.digest.update(encoded)
        self.sources[record['source']] += 1
        self.sites[record.get('source_site') or record['source']] += 1
        self.domains[record.get('topic_domain') or '未分类'] += 1
        self.providers[(record.get('provenance') or {}).get('original_provider') or record['source']] += 1
        self.types[record.get('document_type') or 'verified_fulltext'] += 1
        self.tags.update(record.get('tags') or [])
        self.languages[record['language']] += 1
        for author in record.get('authors') or []:
            if 'version not returned' in author.get('license', ''):
                self.missing_post_licenses[author.get('role', 'unknown')] += 1
        created = (record.get('quality') or {}).get('created_at_epoch')
        if isinstance(created, int):
            domain = record.get('topic_domain') or '未分类'
            previous = self.creation_ranges.get(domain, (created, created))
            self.creation_ranges[domain] = min(previous[0], created), max(previous[1], created)
        self.characters += len(record['body_text'])
        byte_count = len(record['body_text'].encode('utf-8')); self.bytes_count += byte_count
        self.max_document_bytes = max(self.max_document_bytes, byte_count)
        self.paragraph_blocks += len([p for p in record['body_text'].split('\n\n') if p.strip()])
        self.answers += (record.get('quality') or {}).get('answers_received', 0)
        self.media_docs += bool((record.get('quality') or {}).get('contains_media'))
        return True

    @property
    def count(self):
        return len(self.identities)

    def finish(self, **extra):
        self.stream.close()
        self.temporary.replace(self.root / 'documents.jsonl')
        report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'valid_documents': self.count,
                  'corpus_sha256': self.digest.hexdigest(), 'sources': dict(self.sources), 'source_sites': dict(self.sites),
                  'original_providers': dict(self.providers), 'topic_domains': dict(self.domains), 'document_types': dict(self.types),
                  'top_tags': dict(self.tags.most_common(40)), 'duplicates': dict(self.duplicates), 'rejected': dict(self.rejected),
                  'languages': dict(self.languages), 'post_license_versions_unavailable': dict(self.missing_post_licenses),
                  'creation_date_ranges': {domain: [datetime.fromtimestamp(value, timezone.utc).isoformat() for value in bounds] for domain, bounds in self.creation_ranges.items()},
                  'characters': self.characters, 'body_utf8_bytes': self.bytes_count, 'paragraph_blocks': self.paragraph_blocks,
                  'complete_answers': self.answers, 'documents_with_media_references': self.media_docs,
                  'maximum_document_bytes': self.max_document_bytes,
                  'counting_rule': 'one complete Q&A thread or official page is one document; answers, paragraphs and translations are not additional documents',
                  'limitations': ['Accepted answers and nonnegative scores are selection signals, not correctness labels.',
                                  'Text and code are complete; external images are linked, not OCR-processed.',
                                  'Newest accepted-question sampling does not represent all computer technology topics.',
                                  'Question license version can be absent from the API; per-post returned licenses and policy are retained.'], **extra}
        (self.root / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report
