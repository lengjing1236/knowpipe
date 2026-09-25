"""Independently check a frozen corpus against cached API bodies and code blocks."""
import argparse
import hashlib
import json
from collections import Counter
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from ..recommendations.importer import validate_record


class _CodeBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active, self.parts, self.blocks = False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.active, self.parts = True, []
        elif tag == 'br' and self.active:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'pre' and self.active:
            self.blocks.append(''.join(self.parts).replace('\r\n', '\n'))
            self.active = False

    def handle_data(self, text):
        if self.active:
            self.parts.append(text)


def verify(root):
    root = Path(root)
    declared = json.loads((root / 'manifest.json').read_text())
    digest, identities, providers = hashlib.sha256(), set(), Counter()
    checked_answers = checked_code_blocks = official_documents = official_code_blocks = 0

    @lru_cache(maxsize=2)
    def blob(url):
        key = hashlib.sha256(url.encode()).hexdigest()
        raw = (root / 'responses' / (key + '.raw')).read_bytes()
        meta = json.loads((root / 'responses' / (key + '.json')).read_text())
        if hashlib.sha256(raw).hexdigest() != meta['raw_sha256']:
            raise ValueError('response_hash_mismatch')
        return raw, meta['raw_sha256']

    @lru_cache(maxsize=2)
    def response(url):
        raw, checksum = blob(url)
        return {str(item['question_id']): item for item in json.loads(raw)['items']}, checksum

    with (root / 'documents.jsonl').open('rb') as stream:
        for line in stream:
            digest.update(line)
            record = json.loads(line); validate_record(record)
            key = record['source'], record['doc_id']
            if key in identities:
                raise ValueError('duplicate_identity')
            identities.add(key)
            providers[record['provenance']['original_provider']] += 1
            if record['source'] != 'stackexchange':
                raw, checksum = blob(record['provenance']['fetch_url'])
                if checksum != record['provenance']['raw_sha256']:
                    raise ValueError('official_record_provenance_mismatch')
                parser = _CodeBlocks(); parser.feed(raw.decode('utf-8')); parser.close()
                for block in parser.blocks:
                    if block.strip() and block not in record['body_text']:
                        raise ValueError('official_code_changed_in_' + record['doc_id'])
                    official_code_blocks += 1
                license_artifact = record['provenance'].get('license_artifact')
                if license_artifact and blob(license_artifact['fetch_url'])[1] != license_artifact['raw_sha256']:
                    raise ValueError('official_license_provenance_mismatch')
                if license_artifact and license_artifact.get('local_filename'):
                    name = license_artifact['local_filename']
                    if Path(name).name != name or hashlib.sha256((root / name).read_bytes()).hexdigest() != license_artifact['stored_sha256']:
                        raise ValueError('official_license_copy_mismatch')
                official_documents += 1
                continue
            raw_items, raw_hash = response(record['provenance']['fetch_url'])
            if raw_hash != record['provenance']['raw_sha256']:
                raise ValueError('record_provenance_mismatch')
            original = raw_items[record['doc_id'].rsplit('-', 1)[1]]
            if len(original['answers']) != original['answer_count']:
                raise ValueError('original_answer_collection_incomplete')
            if record['quality']['answers_received'] != original['answer_count']:
                raise ValueError('record_answer_count_mismatch')
            expected_ids = {str(original['question_id'])} | {str(answer['answer_id']) for answer in original['answers']}
            if expected_ids != {author['post_id'] for author in record['authors']}:
                raise ValueError('missing_post_attribution')
            for post in [original] + original['answers']:
                parser = _CodeBlocks(); parser.feed(post['body']); parser.close()
                for block in parser.blocks:
                    if block.strip() and block not in record['body_text']:
                        raise ValueError('code_changed_in_' + record['doc_id'])
                    checked_code_blocks += 1
            checked_answers += len(original['answers'])
    if digest.hexdigest() != declared['corpus_sha256'] or len(identities) != declared['valid_documents']:
        raise ValueError('corpus_manifest_mismatch')
    return {'verified_documents': len(identities), 'verified_original_answers': checked_answers,
            'verified_code_blocks': checked_code_blocks, 'verified_official_documents': official_documents,
            'verified_official_code_blocks': official_code_blocks, 'original_providers': dict(providers),
            'corpus_sha256': digest.hexdigest(), 'validation': 'cached source response hashes, answer completeness, author coverage and exact code preservation',
            'does_not_establish': 'answer correctness, learning benefit, image understanding, translation quality'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='state/feature009/corpus')
    parser.add_argument('--output')
    args = parser.parse_args()
    result = verify(args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
