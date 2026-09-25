"""Import explicitly verified full documents from a local JSONL export."""
import argparse
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..learning.content import publish_fulltext, _valid_text
from ..learning.store import ensure_indexes


def validate_record(record):
    fields = ('source', 'doc_id', 'title', 'body_text', 'language', 'source_url', 'license')
    if not isinstance(record, dict) or record.get('fulltext_verified') is not True:
        raise ValueError('fulltext_verification_required')
    if any(not isinstance(record.get(k), str) or not record[k].strip() for k in fields):
        raise ValueError('missing_fulltext_metadata')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', record['source']) or len(record['doc_id']) > 512 or any(ord(c) < 32 for c in record['doc_id']):
        raise ValueError('invalid_identity')
    if not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*', record['language']):
        raise ValueError('invalid_language')
    url = urlsplit(record['source_url'])
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username:
        raise ValueError('invalid_source_url')
    _valid_text(record['body_text'])
    # Optional source metadata is bounded and checked before any database mutation.
    if len(json.dumps({k: record[k] for k in ('authors', 'quality', 'provenance') if k in record}, ensure_ascii=False).encode()) > 1_000_000:
        raise ValueError('source_metadata_too_large')
    for field in ('document_type', 'source_site', 'topic_domain'):
        if field in record and (not isinstance(record[field], str) or len(record[field]) > 256):
            raise ValueError('invalid_source_metadata')
    if 'tags' in record and (not isinstance(record['tags'], list) or len(record['tags']) > 200 or any(not isinstance(tag, str) or len(tag) > 200 for tag in record['tags'])):
        raise ValueError('invalid_source_tags')
    for field in ('quality', 'provenance'):
        if field in record and not isinstance(record[field], dict):
            raise ValueError('invalid_source_metadata')
    if 'authors' in record and (not isinstance(record['authors'], list) or any(not isinstance(author, dict) or not isinstance(author.get('name'), str) for author in record['authors'])):
        raise ValueError('invalid_source_authors')
    for author in record.get('authors') or []:
        if len(author['name']) > 1000:
            raise ValueError('invalid_source_authors')
        for field in ('url', 'post_url', 'license_policy_url'):
            link = author.get(field)
            if link is not None:
                if not isinstance(link, str) or len(link) > 4000:
                    raise ValueError('invalid_source_author_url')
                parts = urlsplit(link)
                if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username:
                    raise ValueError('invalid_source_author_url')
    return record


def import_record(db, record):
    validate_record(record)
    key = {k: record[k] for k in ('source', 'doc_id')}
    now = datetime.now(timezone.utc)
    metadata = {k: record[k] for k in ('title', 'source_url', 'license', 'source_site', 'document_type', 'topic_domain', 'tags', 'authors', 'quality') if k in record}
    db.documents.update_one(key, {'$set': metadata,
                                 '$setOnInsert': {'created_at': now}}, upsert=True)
    result = publish_fulltext(db, **key, text=record['body_text'], language=record['language'])
    db.documents.update_one(key, {'$set': {'fulltext_provenance': {
        'method': 'verified_jsonl', 'imported_at': now,
        'source_url': record['source_url'], 'license': record['license'],
        'content_version': result['content']['version'], 'adapter': record.get('provenance', {})}}})
    return db.documents.find_one(key)


def import_file(db, path):
    ensure_indexes(db)
    count = 0
    with open(path, encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                import_record(db, json.loads(line))
            except (ValueError, TypeError) as error:
                raise ValueError(f'invalid_record_at_line_{number}') from error
            count += 1
    return count


def main():
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--mongo-uri', default=os.environ.get('MONGO_URI', 'mongodb://localhost:27017'))
    parser.add_argument('--mongo-db', default=os.environ.get('MONGO_DB', 'knowpipe_mining'))
    args = parser.parse_args()
    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        print(json.dumps({'imported': import_file(client[args.mongo_db], args.input)}))


if __name__ == '__main__':
    main()
