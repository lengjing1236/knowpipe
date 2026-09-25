import unittest
import mongomock
from knowpipe.recommendations.importer import import_record
from knowpipe.learning.content import content_view


class ImporterTests(unittest.TestCase):
    def test_requires_provenance_and_invalidates_changed_translation(self):
        db = mongomock.MongoClient().db
        record = {'source': 'docs', 'doc_id': '1', 'title': '数据库事务', 'body_text': '事务提交与回滚保存数据',
                  'language': 'zh', 'source_url': 'https://example.com/docs', 'license': 'test fixture',
                  'fulltext_verified': True}
        for delta in ({'fulltext_verified': False}, {'license': ''}, {'source': '$illegal'}, {'language': 'invalid_language'}):
            with self.assertRaises(ValueError):
                import_record(db, dict(record, **delta))
        self.assertEqual(db.documents.count_documents({}), 0)
        first = import_record(db, record)
        second = import_record(db, record)
        self.assertEqual(first['content']['version'], second['content']['version'])
        self.assertTrue(content_view(second)['chinese_ready'])
        changed = import_record(db, dict(record, body_text='事务可原子提交，也可以回滚修改'))
        self.assertNotEqual(first['content']['version'], changed['content']['version'])
        self.assertEqual(db.documents.count_documents({}), 1)

    def test_index_artifact_is_bound_to_original_version(self):
        from knowpipe.learning.content import publish_fulltext, content_version
        db = mongomock.MongoClient().db
        db.documents.insert_one({'source': 'docs', 'doc_id': '1'})
        doc = publish_fulltext(db, 'docs', '1', '数据库事务提交与回滚', 'zh')
        db.documents.update_one({'doc_id': '1'}, {'$set': {'recommendation_analysis': {
            'corpus_id': 'indexed', 'content_version': content_version(doc)}}})
        self.assertEqual(content_view(db.documents.find_one({}))['processing']['analysis']['status'], 'ready')
        publish_fulltext(db, 'docs', '1', '数据库事务隔离级别', 'zh')
        self.assertNotEqual(content_view(db.documents.find_one({}))['processing']['analysis']['status'], 'ready')
