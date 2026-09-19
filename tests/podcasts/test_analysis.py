import unittest

from knowpipe.podcasts.analysis import analyze_transcript, split_transcript


class TranscriptSparkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master('local[2]').appName('test-podcast-transcript')
                     .config('spark.sql.shuffle.partitions', '2').getOrCreate())

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_chinese_and_english_real_spark(self):
        text = ('分布式计算处理大规模数据，数据库索引提升查询效率。Spark processes distributed data.\n' * 12
                + '播客节目讨论科学教育和学习，教师帮助学生学习知识。Podcast education learning science.\n' * 12)
        result = analyze_transcript(self.spark, text)
        self.assertGreater(len(result['segments']), 1)
        self.assertTrue(result['spark_application_id'])
        terms = {k['term'] for segment in result['segments'] for k in segment['keywords']}
        self.assertTrue(any(any('\u4e00' <= c <= '\u9fff' for c in term) for term in terms))
        for segment in result['segments']:
            self.assertTrue(segment['keywords'])
            self.assertTrue(all(0 <= item['score'] <= 1.00001 for item in segment['similar_segments']))

    def test_single_segment_and_empty_input(self):
        result = analyze_transcript(self.spark, 'Spark processes distributed data and MongoDB stores documents.')
        self.assertEqual(len(result['segments']), 1)
        self.assertTrue(result['segments'][0]['keywords'])
        self.assertEqual(result['topic_mode'], 'single_group')
        self.assertEqual(result['segments'][0]['similar_segments'], [])
        with self.assertRaises(ValueError):
            split_transcript('   ')
        with self.assertRaises(ValueError):
            split_transcript('a' * 400001)
