import unittest

from knowpipe.recommendations.text import tokenize, paragraphs, document_key


class TextTests(unittest.TestCase):
    def test_explicit_bilingual_terms_and_boilerplate(self):
        self.assertIn('persistence', tokenize('理解 Redis 持久化'))
        self.assertIn('redis', tokenize('Redis PERSISTENCE'))
        self.assertEqual(tokenize('欢迎收听 今天我们 感谢订阅'), [])

    def test_offsets_point_to_original_even_for_long_text(self):
        text = '开头说明\n\nRedis 持久化保存内存数据。\n\n' + '事务可以提交，也可以回滚。' * 200
        parts = paragraphs('key', 'version', text)
        self.assertTrue(parts)
        for part in parts:
            self.assertEqual(part['text'], text[part['start']:part['end']])
            self.assertLessEqual(len(part['text']), 1200)
        self.assertEqual(document_key('a', '1'), document_key('a', '1'))
        self.assertNotEqual(document_key('a', '1'), document_key('b', '1'))

    def test_english_podcast_boilerplate_is_not_substantive_evidence(self):
        opening = 'Welcome to the AWS podcast. Thanks for listening and subscribing.'
        self.assertEqual(paragraphs('key', 'version', opening), [])
        self.assertEqual(paragraphs('key', 'version', 'Please subscribe to our podcast. Thanks for listening!'), [])
        # Listening and subscribing also have technical meanings; do not
        # globally erase these words from networking or messaging documents.
        technical = 'The server opens a listening socket and clients subscribe to a Kafka topic.'
        self.assertEqual(len(paragraphs('key', 'version', technical)), 1)
        mixed = opening + ' Amazon S3 stores objects in buckets with versioning enabled.'
        self.assertEqual(paragraphs('key', 'version', mixed)[0]['text'], mixed)
        same_sentence = 'Welcome to the AWS podcast where we explain how S3 object versioning preserves old objects.'
        self.assertEqual(paragraphs('key', 'version', same_sentence)[0]['text'], same_sentence)
