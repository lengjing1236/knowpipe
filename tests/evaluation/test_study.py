import unittest
from knowpipe.evaluation.study import import_labels, render_html


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'study_id':'s','k':1,'queries':[
            {'query_id':q,'task':'Learn Spark','profile':{},'personalized':['a'],'baseline':['b'],
             'candidates':[{'id':'a','title':'</script><script>alert(1)</script>','body_text':'Spark','source_url':'https://example.org'},
                           {'id':'b','title':'B','body_text':'Linux','source_url':'https://example.org'}]}
            for q in ['q1','q2']]}
        self.labels={'study_id':'s','reviewer':'human','reviewed_at':'2026-09-19T12:00:00Z',
                     'queries':[{'query_id':q,'judgments':{'a':3,'b':0}} for q in ['q1','q2']]}

    def test_import_real_labels_computes_metrics(self):
        report=import_labels(self.manifest,self.labels)
        self.assertEqual(report['personalized']['precision_at_k'],1)
        self.assertEqual(report['baseline']['precision_at_k'],0)

    def test_rejects_partial_extra_and_wrong_study(self):
        self.labels['queries'][0]['judgments']['a']=None
        with self.assertRaises(ValueError): import_labels(self.manifest,self.labels)
        self.labels['queries'][0]['judgments']={'a':3,'b':0,'unknown':1}
        with self.assertRaises(ValueError): import_labels(self.manifest,self.labels)
        self.labels['study_id']='other'
        with self.assertRaises(ValueError): import_labels(self.manifest,self.labels)

    def test_blind_html_hides_rankings_and_escapes_content(self):
        html=render_html(self.manifest)
        self.assertNotIn('personalized',html)
        self.assertNotIn('baseline',html)
        self.assertNotIn('</script><script>alert',html)
        self.assertIn('请选择',html)
