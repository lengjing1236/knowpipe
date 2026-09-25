"""Real Spark delta-index contract: unchanged vectors reused; versions/deletions fenced."""
import tempfile
import unittest
import mongomock
from knowpipe.learning.content import publish_fulltext
from knowpipe.recommendations.index import prepare_snapshot, build_index, load_index

class IncrementalIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark=(SparkSession.builder.master('local[2]').appName('knowpipe-incremental-test')
                   .config('spark.sql.shuffle.partitions','2').getOrCreate())
        cls.spark.sparkContext.setLogLevel('ERROR')

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_delta_reuses_weights_and_removes_changed_or_deleted_versions(self):
        db=mongomock.MongoClient().test
        for i in range(12):
            db.documents.insert_one({'source':'docs','doc_id':str(i),'title':f'Document {i}','source_url':'https://example.org'})
            publish_fulltext(db,'docs',str(i),'Redis persistence snapshots durable recovery append log disk storage.','en')
        with tempfile.TemporaryDirectory() as root:
            first=build_index(self.spark,prepare_snapshot(db,root))
            old=load_index(self.spark,first)
            postings={tuple(r) for r in old.postings.collect()}
            old_parts={r.doc_id:r.pid for r in old.paragraphs.collect()}
            old.close()
            publish_fulltext(db,'docs','0','Redis append log storage durable recovery persistence snapshots polars.','en')
            second=build_index(self.spark,prepare_snapshot(db,root),previous=first)
            self.assertEqual(second['strategy'],'incremental')
            self.assertEqual(second['reused_documents'],11)
            new=load_index(self.spark,second)
            after={tuple(r) for r in new.postings.collect()}
            self.assertTrue(any(r[1]=='polars' for r in after))
            self.assertGreater(second['added_terms_count'],0)
            self.assertEqual({r for r in postings if r[0]!=old_parts['0']},
                             {r for r in after if r[0] in set(old_parts.values())})
            self.assertNotIn(old_parts['0'],{r[0] for r in after})
            new.close()
            db.documents.delete_one({'source':'docs','doc_id':'1'})
            third=build_index(self.spark,prepare_snapshot(db,root),previous=second)
            self.assertEqual(third['strategy'],'incremental')
            final=load_index(self.spark,third)
            self.assertFalse(final.paragraphs.filter("doc_id='1'").count())
            final.close()
            # A damaged incremental cache must rebuild, and its new IDF basis
            # must have a distinct feature identity despite identical corpus text.
            from pathlib import Path
            (Path(third['index_path'])/'postings'/'_SUCCESS').unlink()
            repaired=build_index(self.spark,prepare_snapshot(db,root))
            self.assertEqual(repaired['strategy'],'full')
            self.assertNotEqual(repaired['feature_id'],third['feature_id'])
            third=repaired
            publish_fulltext(db,'docs','2','Kubernetes scheduling orchestration networking containers clusters nodes.','en')
            fourth=build_index(self.spark,prepare_snapshot(db,root),previous=third)
            self.assertEqual(fourth['strategy'],'full')
            self.assertEqual(fourth['rebuild_reason'],'new_vocabulary')

if __name__=='__main__': unittest.main()
