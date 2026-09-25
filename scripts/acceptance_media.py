#!/usr/bin/env python3
"""Opt-in download and real local inference; never substitutes a model test double.

Run from repository root: python3 scripts/acceptance_media.py --download-models
The cache is ignored by Git. Evidence contains hashes, public URLs and real output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowpipe.learning.local_providers import LocalTranscriber, LocalTranslator
from knowpipe.podcasts.audio import AudioLimits, download_audio, normalize_audio, validate_audio

ARGOS_URL = 'https://argos-net.com/v1/translate-en_zh-1_9.argosmodel'
ARGOS_HASH = '433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
WHISPER_REVISION = 'd90ca5fe260221311c53c58e660288d3deb8d356'
WHISPER_FILES = {
    'model.bin': (75538270, 'dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1'),
    'config.json': (2249, 'a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8'),
    'tokenizer.json': (2203239, 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab'),
    'vocabulary.txt': (459861, '34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913'),
}
AUDIO_URL = 'https://raw.githubusercontent.com/SYSTRAN/faster-whisper/v1.2.1/tests/data/jfk.flac'
SOURCE_TEXT = '''Database transactions keep related updates together. A transaction commits all changes when it succeeds. If a database operation fails, roll back the transaction instead of saving only part of the changes.

```python
connection.execute("INSERT INTO notes (title) VALUES (?)", ("learning",))
connection.commit()
```

An index can speed up searches, but it also adds storage and update cost. Measure queries before adding an index.

End of the document: always close a database connection.'''
REFERENCE_TRANSCRIPT = ('And so my fellow Americans ask not what your country can do for you '
                        'ask what you can do for your country')
TECHNICAL_FEED = 'https://feeds.transistor.fm/aws-morning-brief'
TECHNICAL_GUID = '20edf1f8-433b-4019-aead-2107d801a653'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def fetch(url, path, max_bytes, expected_hash=None, expected_size=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and (expected_hash is None or digest(path) == expected_hash):
        if expected_size is None or path.stat().st_size == expected_size:
            return
    partial = path.with_suffix(path.suffix + '.part')
    request = urllib.request.Request(url, headers={'User-Agent': 'Knowpipe model acceptance/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=40) as response, partial.open('wb') as output:
            if response.status != 200:
                raise ValueError('unexpected_download_status')
            size = 0
            for chunk in iter(lambda: response.read(65536), b''):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError('download_too_large')
                output.write(chunk)
        if expected_size is not None and partial.stat().st_size != expected_size:
            raise ValueError('download_size_mismatch')
        if expected_hash is not None and digest(partial) != expected_hash:
            raise ValueError('download_checksum_mismatch')
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def acquire_models(cache, mirror):
    archive = cache / 'translate-en_zh-1_9.argosmodel'
    fetch(ARGOS_URL, archive, 80_000_000, ARGOS_HASH, 70743021)
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 1000 or sum(item.file_size for item in infos) > 512_000_000:
            raise ValueError('invalid_model_archive')
        for item in infos:
            destination = (cache / item.filename).resolve()
            if cache.resolve() not in destination.parents or item.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError('invalid_model_archive')
        package.extractall(cache)
    whisper = cache / 'faster-whisper-tiny'
    for name, (size, checksum) in WHISPER_FILES.items():
        url = f'{mirror.rstrip("/")}/Systran/faster-whisper-tiny/resolve/{WHISPER_REVISION}/{name}'
        fetch(url, whisper / name, size, checksum, size)
    return whisper


def word_error_rate(reference, hypothesis):
    import re
    expected = re.findall(r'[a-z]+', reference.lower())
    actual = re.findall(r'[a-z]+', hypothesis.lower())
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, start=1):
        current = [i]
        for j, output in enumerate(actual, start=1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (word != output)))
        previous = current
    return previous[-1] / len(expected)


def prepare_technical_audio(cache):
    """Keep a complete real RSS episode, with an explicitly selected item snapshot."""
    metadata_path = cache / 'aws-episode-source.json'
    audio = cache / 'aws-tmpfs-episode.mp3'
    snapshot = cache / 'aws-episode-rss-item.xml'
    if metadata_path.exists() and snapshot.exists() and audio.exists():
        metadata = json.loads(metadata_path.read_text())
    else:
        parser = ET.XMLPullParser(events=['end'])
        selected = None
        channel_title = channel_language = None
        with urllib.request.urlopen(TECHNICAL_FEED, timeout=30) as response:
            for _ in range(512):  # 16 MiB feed bound, stopping once the full item arrives.
                chunk = response.read(32768)
                if not chunk:
                    break
                parser.feed(chunk)
                for _, item in parser.read_events():
                    if item.tag == 'title' and channel_title is None:
                        channel_title = item.text
                    if item.tag == 'language' and channel_language is None:
                        channel_language = item.text
                    if item.tag == 'item' and item.findtext('guid') == TECHNICAL_GUID:
                        selected = item
                        break
                if selected is not None:
                    break
        if selected is None:
            raise ValueError('technical_episode_not_found_within_feed_limit')
        root = ET.Element('rss', version='2.0')
        channel = ET.SubElement(root, 'channel')
        ET.SubElement(channel, 'title').text = channel_title
        if channel_language:
            ET.SubElement(channel, 'language').text = channel_language
        channel.append(selected)
        snapshot.write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True))
        metadata = {'feed_url': TECHNICAL_FEED, 'item_guid': TECHNICAL_GUID,
            'title': selected.findtext('title'), 'published_at': selected.findtext('pubDate'),
            'episode_url': selected.findtext('link'), 'audio_url': selected.find('enclosure').get('url'),
            'rss_item_snapshot': snapshot.name,
            'scope': 'One manually selected intact RSS item; reconstructed channel wrapper, not a full live feed.'}
        download_audio(metadata['audio_url'], audio, AudioLimits(download_timeout_seconds=180))
    metadata.setdefault('retrieved_at', datetime.now(timezone.utc).isoformat())
    metadata['duration_seconds'] = validate_audio(audio)
    metadata['audio_bytes'] = audio.stat().st_size
    metadata['audio_sha256'] = digest(audio)
    metadata['rss_snapshot_sha256'] = digest(snapshot)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return metadata, audio


def run_rss_stage(cache, mongo_uri, keep_db):
    """Production RSS/ASR/fulltext publication with real Mongo and real audio fetch."""
    from pymongo import MongoClient
    from knowpipe.podcasts import store
    from knowpipe.podcasts.worker import run_once
    from knowpipe.learning.content import content_view
    from knowpipe.learning.providers import TextResult
    database_name = 'knowpipe_media_acceptance_' + uuid.uuid4().hex[:12]
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    database = client[database_name]
    try:
        client.admin.command('ping')
        print('rss_acceptance_database=' + database_name, flush=True)
        store.ensure_indexes(database)
        feed_id = store.subscribe(database, 'media-acceptance-user', TECHNICAL_FEED)
        def snapshot_fetch(url):
            if url != TECHNICAL_FEED:
                raise ValueError('unexpected_feed_fetch')
            return (cache / 'aws-episode-rss-item.xml').read_bytes(), 'application/rss+xml'
        transcriber = LocalTranscriber(cache / 'faster-whisper-tiny')
        started = time.perf_counter()
        assert run_once(database, fetch=snapshot_fetch, transcriber=transcriber)
        seconds = time.perf_counter() - started
        del transcriber
        episode = database.podcast_episodes.find_one({})
        if not episode or episode.get('status') != 'ready':
            raise RuntimeError('rss_asr_not_ready: ' + str((episode or {}).get('error_code')))
        doc = database.documents.find_one({'source': 'podcast', 'doc_id': episode['episode_id']})
        assert content_view(doc)['content_status'] == 'fulltext'
        assert episode['transcript_origin'] == 'asr' and episode['attempts'] == 1
        database.podcast_feeds.update_one({'feed_id': feed_id}, {'$set': {'next_poll_at': store.now()}})
        # No injected transcriber on the second poll: any unintended repeat ASR
        # would fail. An already published complete episode must be reused.
        assert run_once(database, fetch=snapshot_fetch)
        repeated = database.podcast_episodes.find_one({'episode_id': episode['episode_id']})
        assert repeated['status'] == 'ready' and repeated['attempts'] == 1
        assert database.documents.count_documents({'source': 'podcast'}) == 1
        assert database.batches.count_documents({}) == 1
        return TextResult(doc['body_text'], doc['language']), {
            'database_name': database_name, 'kept_for_root_integration': keep_db,
            'episode_id': episode['episode_id'], 'feed_id': feed_id,
            'content_version': doc['content']['version'], 'rss_asr_seconds': seconds,
            'audio_fetch': 'Production transcribe_url fetched the complete public URL again, no audio test double.',
            'feed_fetch': 'Explicit one-item snapshot from the real publisher feed.',
            'second_poll': 'One document, one batch, one ASR attempt; no duplicate processing.',
            'scope': 'RSS→ASR→fulltext publication only; recommendation and notification require root integration.'}
    finally:
        if not keep_db:
            client.drop_database(database_name)
        client.close()


def run_technical_acceptance(cache, argos, mongo_uri=None, keep_db=False):
    metadata, audio = prepare_technical_audio(cache)
    rss_report = None
    if mongo_uri:
        transcript, rss_report = run_rss_stage(cache, mongo_uri, keep_db)
        asr_seconds = rss_report['rss_asr_seconds']
    else:
        normalized = cache / 'technical-normalized.wav'
        normalized.unlink(missing_ok=True)
        try:
            normalize_audio(audio, normalized)
            start = time.perf_counter()
            transcriber = LocalTranscriber(cache / 'faster-whisper-tiny')
            transcript = transcriber.transcribe(normalized)
            asr_seconds = time.perf_counter() - start
            del transcriber
        finally:
            normalized.unlink(missing_ok=True)
    transcript_path = cache / 'aws-transcript.txt'
    transcript_path.write_text(transcript.text)
    start = time.perf_counter()
    translator = LocalTranslator(argos)
    translated = translator.translate(transcript.text, transcript.language, 'zh')
    translation_seconds = time.perf_counter() - start
    del translator
    translation_path = cache / 'aws-translation-zh.txt'
    translation_path.write_text(translated.text)
    return {'source': metadata, 'asr_seconds': asr_seconds, 'translation_seconds': translation_seconds,
        'asr_timing_scope': 'RSS poll + public audio download + decode + ASR + publication' if mongo_uri else 'Local model load + ASR',
        'rss_production_stage': rss_report,
        'language': transcript.language, 'transcript_characters': len(transcript.text),
        'translation_characters': len(translated.text),
        'transcript_sha256': digest(transcript_path), 'translation_sha256': digest(translation_path),
        'transcript_path': str(transcript_path), 'translation_path': str(translation_path),
        'complete_processing': transcript.complete and translated.complete,
        'quality_status': 'Engineering baseline only; not accepted for instructional reading. No independent ASR/bilingual quality score.',
        'quality_review': 'evidence/009-fulltext-podcast-learning/media-quality-review.md',
        'publication': 'Full publisher-derived transcripts/translations remain in ignored local cache, not Git evidence.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', default='state/feature009/media')
    parser.add_argument('--download-models', action='store_true')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--technical-podcast', action='store_true')
    parser.add_argument('--technical-only', action='store_true', help='Use complete technical podcast instead of the generic speech fixture.')
    parser.add_argument('--rss-mongo-uri', help='Optional local test Mongo for real RSS worker publication.')
    parser.add_argument('--keep-rss-db', action='store_true', help='Keep randomly named acceptance DB for root integration.')
    parser.add_argument('--model-mirror', default='https://hf-mirror.com')
    parser.add_argument('--output', default='evidence/009-fulltext-podcast-learning/media-acceptance.json')
    args = parser.parse_args(argv)
    if args.technical_only and not args.technical_podcast:
        parser.error('--technical-only requires --technical-podcast')
    cache = Path(args.cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    if args.download_models:
        acquire_models(cache, args.model_mirror)
    if args.download_only:
        return
    metadata = list(cache.glob('*/metadata.json'))
    argos = next((path.parent for path in metadata if json.loads(path.read_text()).get('to_code') == 'zh'), None)
    if argos is None:
        raise RuntimeError('translation_model_missing; use --download-models')
    start = time.perf_counter()
    translator = LocalTranslator(argos)
    translation = translator.translate(SOURCE_TEXT, 'en', 'zh')
    translation_seconds = time.perf_counter() - start
    del translator
    audio = cache / 'jfk.flac'
    transcript = None
    duration = asr_seconds = None
    if not args.technical_only:
        fetch(AUDIO_URL, audio, 2_000_000)
        limits = AudioLimits(max_duration_seconds=60)
        duration = validate_audio(audio, limits)
        normalized = cache / 'acceptance-normalized.wav'
        normalized.unlink(missing_ok=True)
        try:
            normalize_audio(audio, normalized, limits)
            start = time.perf_counter()
            transcriber = LocalTranscriber(cache / 'faster-whisper-tiny')
            transcript = transcriber.transcribe(normalized)
            asr_seconds = time.perf_counter() - start
            del transcriber
        finally:
            normalized.unlink(missing_ok=True)
    code = SOURCE_TEXT.split('```')[1]
    assert code in translation.text and translation.text.count('\n\n') == SOURCE_TEXT.count('\n\n')
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(), 'mode': 'real_local_cpu_int8',
        'models': {
            'translation': {'source': ARGOS_URL, 'sha256': ARGOS_HASH, 'bytes': 70743021, 'path': str(argos)},
            'asr': {'repository': 'Systran/faster-whisper-tiny', 'revision': WHISPER_REVISION,
                    'download_mirror': args.model_mirror,
                    'files': {name: {'sha256': digest(cache / 'faster-whisper-tiny' / name), 'bytes': size}
                              for name, (size, _) in WHISPER_FILES.items()}},
        },
        'dependencies': {name: version(name) for name in ('faster-whisper', 'ctranslate2', 'sentencepiece')},
        'translation': {'source_authorship': 'Knowpipe acceptance fixture, original technical prose',
                        'input': SOURCE_TEXT, 'output': translation.text, 'seconds': translation_seconds,
                        'code_preserved': True, 'complete_processing': translation.complete},
        'asr': ({'source': AUDIO_URL, 'sha256': digest(audio), 'duration_seconds': duration,
                'output': transcript.text, 'language': transcript.language, 'seconds': asr_seconds,
                'reference_transcript': REFERENCE_TRANSCRIPT,
                'case_and_punctuation_insensitive_wer': word_error_rate(REFERENCE_TRANSCRIPT, transcript.text),
                'complete_processing': transcript.complete} if transcript else
                {'status': 'not_run', 'reason': 'Technical-only run uses the complete actual podcast below.'}),
        'limits': ['ASR completion verifies actual processing, not transcription accuracy.',
                   'Translation completeness means no omitted input chunks; adequacy requires separate review.',
                   'These CPU baseline models are not a claim of production learning quality.'],
    }
    if args.technical_podcast:
        report['technical_podcast'] = run_technical_acceptance(cache, argos, args.rss_mongo_uri, args.keep_rss_db)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(path), 'translation_seconds': translation_seconds, 'asr_seconds': asr_seconds,
                      'technical_asr_seconds': report.get('technical_podcast', {}).get('asr_seconds'),
                      'wer': report['asr'].get('case_and_punctuation_insensitive_wer')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
