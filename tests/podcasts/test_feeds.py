import socket
import unittest
from unittest.mock import patch

from knowpipe.podcasts.feeds import parse_feed, parse_transcript
from knowpipe.podcasts.network import FetchError, public_target


class FeedTests(unittest.TestCase):
    def test_guid_dedup_transcript_and_no_description_fallback(self):
        xml = b'''<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Test</title>
          <item><guid>x</guid><title>Episode</title><description>This is not a transcript.</description>
          <podcast:transcript url="/t.vtt" type="text/vtt"/></item>
          <item><guid>x</guid><title>Duplicate</title></item>
          <item><guid>y</guid><title>No transcript</title></item></channel></rss>'''
        title, items = parse_feed(xml, 'https://example.com/feed')
        self.assertEqual(title, 'Test')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['transcript_url'], 'https://example.com/t.vtt')
        self.assertIsNone(items[1]['transcript_url'])
        self.assertNotIn('transcript', items[1])

    def test_entity_expansion_rejected(self):
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>', 'https://example.com')

    def test_publisher_html_transcript_excludes_navigation_and_scripts(self):
        xml = b'''<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel>
          <item><guid>react</guid><description>Only show notes</description>
          <podcast:transcript url="/transcript" type="text/html"/></item></channel></rss>'''
        _, items = parse_feed(xml, 'https://example.com/feed')
        self.assertEqual(items[0]['transcript_type'], 'text/html')
        body = b'''<!DOCTYPE html><html><head><title>Page title</title></head><body>
          <nav><p>Subscribe</p></nav><cite>Speaker:</cite>
          <p>React &amp; <strong>TypeScript</strong><br>work together.
          <script>doNotInclude()</script><style>.hidden {}</style></p>
          <footer><p>Copyright</p></footer></body></html>'''
        self.assertEqual(parse_transcript(body, 'text/html'),
                         'Speaker:\nReact & TypeScript\nwork together.')
        with self.assertRaises(ValueError):
            parse_transcript(b'<html><h1>No transcript available</h1></html>', 'text/html')

    def test_vtt_srt_json(self):
        for mime, body in [('text/vtt', b'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello Spark\n'),
                           ('application/x-subrip', b'1\n00:00:01,000 --> 00:00:02,000\nHello Spark\n'),
                           ('application/json', b'{"segments":[{"body":"Hello Spark"}]}')]:
            with self.subTest(mime=mime):
                self.assertEqual(parse_transcript(body, mime), 'Hello Spark')

    def test_rejects_private_mixed_and_credential_targets(self):
        for url in ['http://127.0.0.1/a', 'http://169.254.169.254/', 'http://[::1]/',
                    'file:///etc/passwd', 'https://u:p@example.com', 'http://example.com:27017']:
            with self.subTest(url=url), self.assertRaises(FetchError):
                public_target(url)
        with patch('socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(FetchError):
                public_target('https://example.com')


if __name__ == '__main__':
    unittest.main()

class FetchBoundaryTests(unittest.TestCase):
    def test_redirect_to_private_address_rejected_before_second_connection(self):
        from unittest.mock import MagicMock
        from knowpipe.podcasts.network import fetch_bytes
        response = MagicMock(status=302)
        response.getheader.side_effect = lambda name, default=None: 'http://127.0.0.1/private' if name == 'Location' else default
        connection = MagicMock()
        connection.getresponse.return_value = response
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))
        with patch('socket.getaddrinfo', side_effect=[[public], [private]]), patch('socket.create_connection') as connect, patch('http.client.HTTPConnection', return_value=connection):
            with self.assertRaises(FetchError):
                fetch_bytes('http://example.com/feed')
            connect.assert_called_once_with(('93.184.216.34', 80), timeout=unittest.mock.ANY)

    def test_body_limit_is_enforced_without_content_length(self):
        from unittest.mock import MagicMock
        from knowpipe.podcasts.network import fetch_bytes
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda name, default=None: default
        response.read1.return_value = b'too much'
        connection = MagicMock()
        connection.getresponse.return_value = response
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
        with patch('socket.getaddrinfo', return_value=[public]), patch('socket.create_connection'), patch('http.client.HTTPConnection', return_value=connection):
            with self.assertRaisesRegex(FetchError, 'response_too_large'):
                fetch_bytes('http://example.com/feed', max_bytes=3)
