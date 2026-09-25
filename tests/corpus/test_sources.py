import copy
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import mongomock

from knowpipe.corpus.html import extract_html
from knowpipe.corpus.stackexchange import question_record, CorpusRejected
from knowpipe.corpus.pipeline import CorpusWriter
from knowpipe.corpus.network import CachedClient, FetchStopped
from knowpipe.corpus.official import arxiv_html_record, official_record
from knowpipe.corpus.supplement import supplemental_record, prepare as prepare_supplement, PAGES
from knowpipe.recommendations.importer import import_record


def question():
    return {'question_id': 123, 'title': '事务提交与回滚', 'body': '<p>How do transactions roll back?</p><pre><code>if error:\n    rollback()</code></pre>',
            'link': 'https://stackoverflow.com/questions/123/transactions', 'tags': ['python', 'sqlite'],
            'score': 2, 'answer_count': 1, 'accepted_answer_id': 456, 'creation_date': 1700000000,
            'owner': {'display_name': 'Question author', 'link': 'https://stackoverflow.com/users/1/author'},
            'answers': [{'answer_id': 456, 'question_id': 123, 'is_accepted': True, 'score': 3,
                         'content_license': 'CC BY-SA 4.0', 'body': '<p>Use rollback when the transaction fails.</p><pre><code>try:\n    commit()\nexcept Error:\n    rollback()</code></pre>',
                         'owner': {'display_name': 'Answer author', 'link': 'https://stackoverflow.com/users/2/author'}}]}


class SourceTests(unittest.TestCase):
    def test_html_keeps_code_paragraphs_and_media_and_removes_scripts(self):
        text, images = extract_html('<p>First &amp; second</p><pre><code>if a:\n    b()</code></pre><p>After</p><script>bad()</script><img src="/diagram.png" alt="Flow">', base_url='https://example.org/a')
        self.assertIn('First & second\n\n', text)
        self.assertIn('if a:\n    b()', text)
        self.assertNotIn('bad()', text)
        self.assertIn('https://example.org/diagram.png', text)
        self.assertEqual(images, ['https://example.org/diagram.png'])
        code = 'if a:\n    text = "```"\n\n\n    b()'
        protected, _ = extract_html('<pre><code>' + code + '</code></pre>')
        self.assertIn(code, protected)

    def test_whole_question_and_answer_with_honest_license(self):
        row = question_record(question(), 'stackoverflow', {'raw_sha256': 'abc'})
        self.assertIn('rollback when the transaction fails', row['body_text'])
        self.assertIn('    commit()', row['body_text'])
        self.assertEqual(row['quality']['answers_expected'], 1)
        self.assertEqual(row['quality']['answers_received'], 1)
        self.assertEqual(row['authors'][0]['license'], 'CC BY-SA; version not returned by API')
        self.assertEqual(row['authors'][1]['license'], 'CC BY-SA 4.0')
        self.assertEqual(row['provenance']['raw_sha256'], 'abc')
        self.assertTrue(row['fulltext_verified'])

    def test_missing_answer_or_body_never_claims_fulltext(self):
        for mutate in (lambda q: q.update(answer_count=2), lambda q: q['answers'][0].pop('body'),
                       lambda q: q.update(accepted_answer_id=999), lambda q: q.update(score=-1),
                       lambda q: q.update(answers=[]), lambda q: q.update(closed_date=1700000000)):
            q = question(); mutate(q)
            with self.assertRaises(CorpusRejected):
                question_record(q, 'stackoverflow', {})

    def test_writer_deduplicates_identity_and_complete_body(self):
        with tempfile.TemporaryDirectory() as root:
            writer = CorpusWriter(Path(root))
            row = question_record(question(), 'stackoverflow', {})
            self.assertTrue(writer.add(row))
            self.assertFalse(writer.add(row))
            same = copy.deepcopy(row); same['doc_id'] = 'another'
            self.assertFalse(writer.add(same))
            report = writer.finish()
            self.assertEqual(report['valid_documents'], 1)
            self.assertEqual(report['duplicates'], {'identity': 1, 'body': 1})
            self.assertEqual(len(Path(root, 'documents.jsonl').read_text().splitlines()), 1)

    def test_importer_preserves_credits_and_adapter_provenance(self):
        db = mongomock.MongoClient().db
        row = question_record(question(), 'stackoverflow', {'raw_sha256': 'abc'})
        import_record(db, row)
        doc = db.documents.find_one({})
        self.assertEqual(doc['authors'], row['authors'])
        self.assertEqual(doc['tags'], ['python', 'sqlite'])
        self.assertEqual(doc['document_type'], 'qa_thread')
        self.assertEqual(doc['fulltext_provenance']['adapter']['raw_sha256'], 'abc')

    def test_abstract_or_navigation_is_never_an_official_fulltext(self):
        with self.assertRaises(CorpusRejected):
            official_record('<nav><h1>Title</h1>' + '导航文本' * 1000 + '</nav>', 'python_docs', 'page', 'https://docs.python.org/', 'PSF-2.0', 'https://docs.python.org/license.html', {})
        for html in ('<feed><summary>' + 'An abstract. ' * 500 + '</summary></feed>',
                     '<article class="ltx_document"><h1>Title</h1><div class="ltx_abstract">' + '<p>An abstract.</p>' * 500 + '</div></article>'):
            with self.assertRaises(CorpusRejected):
                arxiv_html_record(html, arxiv_id='2001.00888v4', license_name='CC BY 4.0', provenance={})

    def test_invalid_metadata_fails_before_database_mutation(self):
        db = mongomock.MongoClient().db
        row = question_record(question(), 'stackoverflow', {})
        for change in ({'authors': 'not an author list'}, {'authors': [{'name': 'name', 'url': 'javascript:alert(1)'}]}, {'tags': ['$x' * 201]}, {'provenance': []}):
            with self.assertRaises(ValueError):
                import_record(db, dict(row, **change))
        self.assertEqual(db.documents.count_documents({}), 0)

    def test_supplement_keeps_postgresql_section_and_docker_article_only(self):
        paragraph = 'PostgreSQL transaction isolation controls what concurrent sessions can observe. ' * 12
        for source, opening, closing, heading in ((PAGES[0], '<div class="sect1" id="TRANSACTION-ISO">', '</div>', 'h2'),
                                                   (PAGES[1], '<article class="prose dark:prose-invert">', '</article>', 'h1')):
            html = '<nav>NOISY NAVIGATION</nav>' + opening + '<' + heading + '>Concurrency</' + heading + '><p>' + paragraph + '</p><pre>begin;\n  commit;</pre>' + closing + '<footer>NOISY FOOTER</footer>'
            row = supplemental_record(html, source, source['paths'][0], {'raw_sha256': 'html'}, {'raw_sha256': 'license'})
            self.assertEqual(row['title'], 'Concurrency')
            self.assertNotIn('NOISY', row['body_text'])
            self.assertIn('begin;\n  commit;', row['body_text'])
            self.assertEqual(row['provenance']['license_artifact']['raw_sha256'], 'license')

    def test_supplement_cannot_overwrite_or_nest_in_base_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            for target in (root, str(Path(root, 'nested'))):
                with self.assertRaises(ValueError):
                    prepare_supplement(root, target)


class NetworkTests(unittest.TestCase):
    def test_cached_response_does_not_make_network_request(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=2, min_interval=0)
            url = 'https://api.stackexchange.com/2.3/search/advanced?site=stackoverflow'
            with patch.object(client, '_download', return_value=b'{"items":[],"quota_remaining":1}') as download:
                first = client.json(url)
                second = client.json(url)
            self.assertEqual(first[0], second[0])
            self.assertEqual(download.call_count, 1)

    def test_zero_quota_stops_later_uncached_se_request(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=2, min_interval=0)
            with patch.object(client, '_download', return_value=b'{"items":[],"quota_remaining":0}'):
                client.json('https://api.stackexchange.com/2.3/questions?page=1')
            with self.assertRaises(FetchStopped):
                client.json('https://api.stackexchange.com/2.3/questions?page=2')

    def test_backoff_is_persisted_before_next_request(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=2, min_interval=0)
            with patch.object(client, '_download', return_value=b'{"items":[],"quota_remaining":20,"backoff":2}'):
                client.json('https://api.stackexchange.com/2.3/questions?page=1')
            state = json.loads(Path(root, 'network-state.json').read_text())
            self.assertGreater(state['se_next_allowed'], 0)

    def test_corrupt_cache_is_not_used_and_request_budget_is_finite(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=1, min_interval=0)
            url = 'https://api.stackexchange.com/2.3/questions?page=1'
            with patch.object(client, '_download', return_value=b'{"items":[]}'):
                client.json(url)
            with self.assertRaises(FetchStopped):
                client.json(url.replace('page=1', 'page=2'))
            next(Path(root).glob('*.raw')).write_text('bad cache')
            with self.assertRaises(FetchStopped):
                client.json(url)

    def test_long_backoff_stops_without_request_or_long_sleep(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=1, min_interval=0)
            client.state['se_next_allowed'] = time.time() + 3600
            with patch.object(client, '_download') as request, patch('knowpipe.corpus.network.time.sleep') as sleep:
                with self.assertRaises(FetchStopped):
                    client.json('https://api.stackexchange.com/2.3/questions?page=1')
            request.assert_not_called()
            sleep.assert_not_called()

    def test_transient_retries_are_bounded_and_never_repeat_stackexchange(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, max_requests=5, min_interval=0, transient_retries=2)
            with patch.object(client, '_download', side_effect=[FetchStopped('network_unavailable'), b'public documentation']) as request, patch('knowpipe.corpus.network.time.sleep'):
                raw, _ = client.bytes('https://docs.docker.com/engine/network/')
                self.assertEqual(raw, b'public documentation')
                self.assertEqual(request.call_count, 2)
            with patch.object(client, '_download', side_effect=FetchStopped('network_unavailable')) as request:
                with self.assertRaises(FetchStopped):
                    client.json('https://api.stackexchange.com/2.3/questions?page=1')
                self.assertEqual(request.call_count, 1)

    def test_connection_reset_is_classified_as_transient(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, min_interval=0)
            with patch('knowpipe.corpus.network.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = ConnectionResetError('peer reset')
                with self.assertRaisesRegex(FetchStopped, 'network_unavailable'):
                    client.bytes('https://docs.docker.com/engine/network/')

    def test_http_retry_after_is_persisted_and_prevents_early_retry(self):
        with tempfile.TemporaryDirectory() as root:
            client = CachedClient(root, min_interval=0, transient_retries=2)
            url = 'https://docs.docker.com/engine/network/'
            with patch('knowpipe.corpus.network.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = urllib.error.HTTPError(url, 429, 'limit', {'Retry-After': '3600'}, None)
                with self.assertRaisesRegex(FetchStopped, 'http_429'):
                    client.bytes(url)
                with self.assertRaisesRegex(FetchStopped, 'source_backoff_pending'):
                    client.bytes(url)
                self.assertEqual(opener.return_value.open.call_count, 1)
            self.assertGreater(json.loads(Path(root, 'network-state.json').read_text())['host_next_allowed']['docs.docker.com'], time.time())


if __name__ == '__main__':
    unittest.main()
