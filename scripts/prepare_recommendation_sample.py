"""Fetch a small, explicit official-document sample; never represents the 10k corpus."""
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


class MainText(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    BLOCK = {'p', 'li', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'dt', 'dd', 'tr', 'div', 'section'}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source, self.depth, self.skip = source, 0, 0
        self.parts, self.title_parts = [], []
        self.in_title = False
        self.found = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.depth == 0:
            target = attrs.get('role') == 'main' and 'body' in attrs.get('class', '').split() if self.source == 'python_docs' else attrs.get('id') == 'docs-content'
            if target:
                self.depth, self.found = 1, True
            return
        if tag not in self.VOID:
            self.depth += 1
        if self.skip:
            return
        if tag in {'script', 'style', 'nav'} or 'headerlink' in attrs.get('class', '').split():
            self.skip = self.depth
            return
        if tag == 'h1':
            self.in_title = True
        if tag in self.BLOCK:
            self.parts.append('\n\n')
        elif tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if not self.depth or tag in self.VOID:
            return
        if self.skip:
            if self.depth == self.skip:
                self.skip = 0
        else:
            if tag == 'h1':
                self.in_title = False
            if tag in self.BLOCK:
                self.parts.append('\n\n')
        self.depth -= 1

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)
            if self.in_title:
                self.title_parts.append(data)

    def extract(self, html):
        self.feed(html)
        text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', ''.join(self.parts)).strip()
        title = ' '.join(''.join(self.title_parts).split())
        if not self.found or not title or len(text) < 1000 or len(re.findall(r'[\u4e00-\u9fff]', text)) < 300:
            raise ValueError('main_text_validation_failed')
        return title, text


SOURCES = [
    ('python_docs', 'https://docs.python.org/zh-cn/3/', 'PSF-2.0; examples also 0BSD', 'https://docs.python.org/zh-cn/3/license.html', [
        'tutorial/datastructures.html', 'tutorial/errors.html', 'library/sqlite3.html',
        'library/asyncio-task.html', 'library/asyncio-sync.html', 'library/concurrent.futures.html', 'howto/logging.html']),
    ('django_docs', 'https://docs.djangoproject.com/zh-hans/5.2/', 'BSD-3-Clause', 'https://raw.githubusercontent.com/django/django/stable/5.2.x/LICENSE', [
        'topics/db/transactions/', 'topics/db/queries/', 'topics/db/aggregation/', 'topics/db/optimization/', 'topics/async/'])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='state/feature008/sample')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'fetched_at': datetime.now(timezone.utc).isoformat(), 'purpose': 'small real-text integration sample', 'documents': []}
    records = []
    for source, base, license_name, license_url, paths in SOURCES:
        license_path = output / (source + '-license.txt')
        if not license_path.exists():
            license_path.write_bytes(urllib.request.urlopen(license_url, timeout=30).read())
        for path in paths:
            url = base + path
            raw_path = output / (source + '-' + hashlib.sha256(url.encode()).hexdigest()[:12] + '.html')
            if not raw_path.exists():
                request = urllib.request.Request(url, headers={'User-Agent': 'Knowpipe-course-sample/1.0'})
                raw_path.write_bytes(urllib.request.urlopen(request, timeout=30).read())
            raw = raw_path.read_bytes()
            title, text = MainText(source).extract(raw.decode('utf-8'))
            record = {'source': source, 'doc_id': path.rstrip('/'), 'title': title, 'body_text': text,
                      'language': 'zh', 'source_url': url, 'license': license_name + '; ' + license_url,
                      'fulltext_verified': True}
            records.append(record)
            manifest['documents'].append({k: record[k] for k in ('source', 'doc_id', 'title', 'source_url', 'license')})
            manifest['documents'][-1].update(characters=len(text), han_characters=len(re.findall(r'[\u4e00-\u9fff]', text)),
                text_sha256=hashlib.sha256(text.encode()).hexdigest(), html_sha256=hashlib.sha256(raw).hexdigest())
            print(source, path, len(text), flush=True)
    (output / 'documents.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
