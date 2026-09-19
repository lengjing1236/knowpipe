import unittest
import mongomock
from knowpipe.mining.scale import clean_records, verify_counts


def record(i, source='stackexchange'):
    return dict(doc_id=str(i), source=source, source_site='test', title=str(i),
                body_text='Spark processing '+str(i), language='en', created_at='2026-01-01',
                source_url='https://example.org/'+str(i))


class ScaleTests(unittest.TestCase):
    def test_dedup_and_invalid_have_accounting(self):
        rows, counts = clean_records([record(1), record(1), {}, record(2)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(counts, {'input': 4, 'invalid': 1, 'duplicate': 1, 'valid': 2})

    def test_acceptance_counts_only_joined_current_batch(self):
        db = mongomock.MongoClient().test
        db.documents.insert_one({**record(1), 'mining': {'batch_id':'b'}})
        db.mining_results.insert_one({'source':'stackexchange','doc_id':'1','batch_id':'old'})
        self.assertFalse(verify_counts(db, 'b', minimum=1)['accepted'])
        db.mining_results.insert_one({'source':'stackexchange','doc_id':'1','batch_id':'b'})
        self.assertFalse(verify_counts(db, 'b', minimum=1)['accepted'])
        db.documents.insert_one({**record(2,'arxiv'), 'mining': {'batch_id':'b'}})
        db.mining_results.insert_one({'source':'arxiv','doc_id':'2','batch_id':'b'})
        self.assertTrue(verify_counts(db, 'b', minimum=1)['accepted'])

    def test_duplicate_result_rows_cannot_inflate_acceptance(self):
        db = mongomock.MongoClient().test
        for source in ['stackexchange','arxiv']:
            db.documents.insert_one({**record(source,source),'mining':{'batch_id':'b'}})
            db.mining_results.insert_one({'source':source,'doc_id':source,'batch_id':'b'})
        self.assertTrue(verify_counts(db,'b',minimum=1)['accepted'])
        db.mining_results.insert_one({'source':'stackexchange','doc_id':'stackexchange','batch_id':'b'})
        self.assertFalse(verify_counts(db,'b',minimum=1)['accepted'])
