"""Known official main-content adapters; navigation and abstracts are excluded."""
import re
from .html import TextExtractor
from .stackexchange import CorpusRejected

OFFICIAL_PAGES = (
    ('python_docs', 'https://docs.python.org/zh-cn/3/', 'PSF-2.0; examples also 0BSD', 'https://docs.python.org/zh-cn/3/license.html', (
        'tutorial/datastructures.html', 'tutorial/errors.html', 'library/sqlite3.html',
        'library/asyncio-task.html', 'library/asyncio-sync.html', 'library/concurrent.futures.html', 'howto/logging.html')),
    ('django_docs', 'https://docs.djangoproject.com/zh-hans/5.2/', 'BSD-3-Clause', 'https://raw.githubusercontent.com/django/django/stable/5.2.x/LICENSE', (
        'topics/db/transactions/', 'topics/db/queries/', 'topics/db/aggregation/', 'topics/db/optimization/', 'topics/async/')),
)


def official_record(html, source, path, base_url, license_name, license_url, provenance):
    if source not in {'python_docs', 'django_docs'}:
        raise CorpusRejected('unsupported_official_provider')
    selector = (lambda tag, attrs: attrs.get('role') == 'main' and 'body' in attrs.get('class', '').split()) if source == 'python_docs' else (lambda tag, attrs: attrs.get('id') == 'docs-content')
    parser = TextExtractor(base_url + path, selector)
    parser.feed(html)
    text, images = parser.finish()
    title = ' '.join(''.join(parser.title).split())
    if not parser.found or not title or len(text) < 1000 or len(re.findall(r'[\u4e00-\u9fff]', text)) < 300:
        raise CorpusRejected('official_main_content_missing_or_incomplete')
    provider = 'Python Software Foundation' if source == 'python_docs' else 'Django Software Foundation'
    return {'source': source, 'doc_id': path.rstrip('/'), 'title': title, 'body_text': text,
            'language': 'zh', 'source_url': base_url + path, 'license': license_name + '; ' + license_url,
            'fulltext_verified': True, 'document_type': 'official_document', 'topic_domain': '编程与开发' if source == 'python_docs' else 'Web与数据库',
            'authors': [{'name': provider + ' and contributors', 'url': base_url, 'license': license_name}],
            'tags': ['python'] + (['django'] if source == 'django_docs' else []),
            'quality': {'extraction_method': 'official_html_main_container', 'contains_media': bool(images),
                        'media_urls': images, 'media_content_extracted': False},
            'provenance': {**provenance, 'adapter_version': 'official-main-html-v1', 'original_provider': provider}}


def arxiv_html_record(html, *, arxiv_id, license_name, provenance):
    """Only complete publisher HTML with an explicit supplied license is admissible.

    Metadata feeds and Atom summaries must never be passed through as full text.
    License discovery is a caller responsibility; unknown rights are not invented.
    """
    if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})v\d+', arxiv_id):
        raise CorpusRejected('arxiv_version_required')
    if not isinstance(license_name, str) or not license_name.strip() or license_name.lower() in {'unknown', 'none'}:
        raise CorpusRejected('arxiv_license_required')
    url = 'https://arxiv.org/html/' + arxiv_id
    parser = TextExtractor(url, lambda tag, attrs: tag == 'article' and 'ltx_document' in attrs.get('class', '').split())
    parser.feed(html)
    text, images = parser.finish()
    title = ' '.join(''.join(parser.title).split())
    sections = re.findall(r'<section\b[^>]*\bclass=[\"\'][^\"\']*\bltx_section\b', html, flags=re.I)
    if not parser.found or not title or len(text) < 2000 or text.count('\n\n') < 4 or len(sections) < 2:
        raise CorpusRejected('arxiv_complete_html_unavailable')
    return {'source': 'arxiv', 'doc_id': arxiv_id, 'title': title, 'body_text': text, 'language': 'en',
            'source_url': url, 'license': license_name, 'fulltext_verified': True, 'document_type': 'research_paper',
            'quality': {'extraction_method': 'arxiv_html_article', 'contains_media': bool(images),
                        'media_urls': images, 'media_content_extracted': False},
            'provenance': {**provenance, 'adapter_version': 'arxiv-html-v1', 'original_provider': 'arXiv'}}
