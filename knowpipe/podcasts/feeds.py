"""RSS and Podcasting 2.0 transcript adapters. Descriptions are never transcripts."""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

TRANSCRIPT_NS = '{https://podcastindex.org/namespace/1.0}'
SUPPORTED_TYPES = {'text/plain', 'text/html', 'text/vtt', 'application/x-subrip', 'application/srt',
                   'text/srt', 'application/json'}


class TranscriptHTML(HTMLParser):
    """Read paragraphs from a publisher-declared transcript, excluding page chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocked = []
        self.capture = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'nav', 'header', 'footer', 'aside'}:
            self.blocked.append(tag)
        if not self.blocked:
            if tag in {'p', 'cite'}:
                self.capture = tag
                self.parts.append('\n')
            elif tag == 'br' and self.capture:
                self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag == self.capture:
            self.capture = None
            self.parts.append('\n')

    def handle_data(self, data):
        if self.capture and not self.blocked:
            self.parts.append(data)


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse_feed(data: bytes, url: str, max_items: int = 3):
    try:
        root = fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise ValueError('invalid_feed') from exc
    channel = root.find('channel')
    if channel is None:
        raise ValueError('unsupported_feed')
    title = (channel.findtext('title') or url).strip()[:300]
    items, seen = [], set()
    for item in channel.findall('item'):
        enclosure = item.find('enclosure')
        audio_url = enclosure.get('url') if enclosure is not None else None
        guid = item.findtext('guid') or audio_url or item.findtext('link')
        if not guid or guid in seen:
            continue
        seen.add(guid)
        transcript_url, transcript_type = None, None
        for transcript in item.findall(TRANSCRIPT_NS + 'transcript'):
            mime = transcript.get('type', '').lower().split(';')[0]
            if mime in SUPPORTED_TYPES and transcript.get('url'):
                transcript_url = urljoin(url, transcript.get('url'))
                transcript_type = mime
                break
        try:
            published = parsedate_to_datetime(item.findtext('pubDate') or '')
            published = published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            published = None
        items.append({
            'episode_id': stable_id(stable_id(url) + ':' + guid),
            'feed_id': stable_id(url), 'title': (item.findtext('title') or '未命名节目').strip()[:300],
            'source_url': urljoin(url, item.findtext('link') or url),
            'audio_url': urljoin(url, audio_url) if audio_url else None,
            'transcript_url': transcript_url, 'transcript_type': transcript_type,
            'published_at': published,
        })
    floor = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda item: item['published_at'] or floor, reverse=True)
    return title, items[:max_items]


def parse_transcript(data: bytes, mime: str) -> str:
    try:
        text = data.decode('utf-8-sig')
        if mime == 'application/json':
            parsed = json.loads(text)
            segments = parsed.get('segments', []) if isinstance(parsed, dict) else parsed
            if not isinstance(segments, list):
                raise ValueError('invalid_transcript')
            texts = [(segment.get('body') or segment.get('text') or '')
                     for segment in segments if isinstance(segment, dict)]
            if not all(isinstance(value, str) for value in texts):
                raise ValueError('invalid_transcript')
            text = '\n'.join(texts)
        elif mime in {'text/vtt', 'application/x-subrip', 'application/srt', 'text/srt'}:
            lines = []
            in_note = False
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    in_note = False
                    continue
                if line.startswith(('NOTE', 'STYLE', 'REGION')):
                    in_note = True
                if in_note or line == 'WEBVTT' or '-->' in line or line.isdigit():
                    continue
                lines.append(html.unescape(re.sub(r'<[^>]*>', '', line)))
            text = '\n'.join(lines)
        elif mime == 'text/html':
            parser = TranscriptHTML()
            parser.feed(text)
            text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
        elif mime != 'text/plain':
            raise ValueError('unsupported_transcript')
        text = text.strip()
        if not text or text.lstrip().lower().startswith(('<html', '<!doctype', '<?xml')):
            raise ValueError('invalid_transcript')
        return text
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid_transcript') from exc
