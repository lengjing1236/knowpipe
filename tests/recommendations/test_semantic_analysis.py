import unittest
from unittest.mock import patch

from knowpipe.recommendations.engine import _choose, recommend, _context_distance
from knowpipe.recommendations.semantic_analysis import analyze, sentences, substantive
from knowpipe.recommendations.semantic import EnglishText


HISTORY = 'Database snapshots preserve the committed data so recovery can restore a consistent state after failure.'
COVERED = 'A saved database snapshot can restore the committed records to a consistent state following a server failure.'
EXTRA = 'An operation log records each committed change so recovery can replay updates made after the latest snapshot.'
CONFLICT = 'Database snapshots never contain any committed records and therefore cannot restore data after a failure.'


def part(key, text, start=0):
    return {'doc_key': key, 'source': 'test', 'doc_id': key, 'title': 'Recovery explanations',
            'content_version': 'version', 'pid': key + str(start), 'start': start,
            'end': start + len(text), 'text': text}


class Features:
    processor_id = 'explicit-test-features'
    text = EnglishText()

    def relevance(self, query, passages):
        return [.02 if 'flowers' in text else .9 for text in passages]

    def encode(self, texts):
        return [[0., 1.] if 'flowers' in text else [1., 0.] for text in texts]

    def infer(self, pairs):
        return [dict(contradiction=.98, entailment=.01, neutral=.01) if candidate == CONFLICT
                else dict(contradiction=.01, entailment=.98, neutral=.01) if candidate in (HISTORY, COVERED)
                else dict(contradiction=.01, entailment=.01, neutral=.98)
                for _, candidate in pairs]


class SemanticAnalysisTests(unittest.TestCase):
    def test_literal_offsets_and_substantive_evidence(self):
        body = 'Snapshot heading\n' + HISTORY + ' ' + EXTRA
        rows = sentences(part('doc', body, 20))
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['text'], body[row['start'] - 20:row['end'] - 20])
        self.assertEqual(sentences(part('title', 'Database recovery')), [])
        self.assertEqual(sentences(part('question', 'How can the database recover its most recently committed transactions after a sudden server failure?')), [])
        self.assertTrue(substantive('信号量使用内部计数器限制同时访问共享资源的协程数量。'))
        self.assertFalse(substantive('信号量与协程资源限制'))
        scope = {}
        self.assertEqual(sentences(part('long', 'word ' * 300 + '.'), scope), [])
        self.assertEqual(scope['oversized_sentences'], 1)

    def test_partial_coverage_preserves_new_statement_and_both_sides(self):
        text = COVERED + ' ' + EXTRA
        result = analyze(Features(), 'database recovery', [part('mixed', text, 11)], [part('history', HISTORY, 7)])
        doc = result['documents']['mixed']
        comparison = doc['comparison']
        self.assertEqual(comparison['status'], 'possible_supplement')
        self.assertEqual(len(comparison['covered_evidence']), 1)
        self.assertEqual(comparison['candidate']['text'], EXTRA)
        self.assertEqual(comparison['history']['text'], HISTORY)
        self.assertGreater(doc['supplement_score'], 0.)
        self.assertEqual(doc['history_overlap'], .5)
        ev = comparison['candidate']
        self.assertEqual(ev['text'], text[ev['start'] - 11:ev['end'] - 11])

    def test_history_inference_uses_source_paragraph_including_adjacent_fact(self):
        class ContextFeatures(Features):
            def infer(self, pairs):
                self.seen = pairs
                return [dict(contradiction=.01, entailment=.98, neutral=.01)
                        if EXTRA in premise else dict(contradiction=.01, entailment=.01, neutral=.98)
                        for premise, hypothesis in pairs]

        features = ContextFeatures()
        complete = HISTORY + ' ' + EXTRA
        result = analyze(features, 'database recovery', [part('candidate', EXTRA)], [part('read', complete, 17)])
        comparison = result['documents']['candidate']['comparison']
        self.assertEqual(comparison['status'], 'covered')
        self.assertTrue(all(premise == complete for premise, _ in features.seen))
        self.assertEqual(comparison['history']['text'], complete)
        self.assertEqual(comparison['history']['start'], 17)

    def test_dependent_neutral_clause_keeps_context_but_cannot_claim_supplement(self):
        dependent = 'This approach also preserves additional committed changes after a crash, using the operation log described above.'
        candidate = COVERED + ' ' + dependent
        result = analyze(Features(), 'database recovery', [part('candidate', candidate, 5)], [part('read', HISTORY)])
        comparison = result['documents']['candidate']['comparison']
        self.assertEqual(comparison['status'], 'uncertain')
        item = comparison['uncertain_evidence'][0]
        self.assertTrue(item['context_dependent'])
        self.assertEqual(item['candidate']['text'], candidate)
        self.assertEqual(item['focus']['text'], dependent)

    def test_overlapping_context_anchors_do_not_change_nearest_distance(self):
        anchors = [{'start': 100, 'end': 200}, {'start': 150, 'end': 250}]
        self.assertEqual(_context_distance(190, 230, anchors), 0)
        self.assertEqual(_context_distance(300, 350, anchors), 50)
        self.assertEqual(_context_distance(20, 50, anchors), 50)

    def test_paraphrase_covered_and_conflict_cannot_be_supplement(self):
        result = analyze(Features(), 'database recovery', [part('copy', COVERED), part('conflict', CONFLICT)], [part('history', HISTORY)])
        self.assertEqual(result['documents']['copy']['comparison']['status'], 'covered')
        self.assertEqual(result['documents']['conflict']['comparison']['status'], 'uncertain')
        self.assertEqual(result['documents']['conflict']['supplement_score'], 0.)

    def test_prior_foundation_need_not_answer_goal_but_unrelated_history_is_not_support(self):
        class FoundationFeatures(Features):
            def relevance(self, query, passages):
                return [.001 if text == HISTORY else .9 for text in passages]

        result = analyze(FoundationFeatures(), 'new database recovery method', [part('candidate', EXTRA)], [part('read', HISTORY)])
        self.assertEqual(result['documents']['candidate']['comparison']['status'], 'possible_supplement')
        garden = 'The flowers in the garden need regular watering and nutrients to grow healthy leaves and strong roots.'
        result = analyze(Features(), 'database recovery', [part('candidate', EXTRA)], [part('read', garden)])
        self.assertEqual(result['documents']['candidate']['comparison']['status'], 'uncertain')
        self.assertEqual(result['documents']['candidate']['supplement_score'], 0.)

    def test_unrelated_new_words_rejected_and_absent_history_is_not_novelty(self):
        unrelated = 'Beautiful flowers in the garden grow rapidly with fresh soil and special watering techniques every morning.'
        result = analyze(Features(), 'database recovery', [part('extra', EXTRA), part('garden', unrelated)], [])
        self.assertNotIn('garden', result['documents'])
        self.assertEqual(result['documents']['extra']['comparison']['status'], 'goal_only')
        self.assertEqual(result['documents']['extra']['supplement_score'], 0.)

    def test_multilingual_rank_and_vectors_keep_original_goal_and_material(self):
        class NativeFeatures(Features):
            language = 'multilingual'

            def relevance(self, query, passages):
                self.last_goal = query
                return super().relevance(query, passages)

        class DoNotTranslate:
            def convert(self, text):
                raise AssertionError('No translation is needed without history.')

        native = NativeFeatures()
        native.text = DoNotTranslate()
        result = analyze(native, '数据库故障恢复', [part('extra', EXTRA)], [])
        self.assertEqual(native.last_goal, '数据库故障恢复')
        self.assertEqual(result['language'], 'multilingual')
        self.assertIn('extra', result['documents'])

    def test_supplement_changes_real_selection_and_history_ablation_removes_it(self):
        candidates = [dict(doc_key='covered', relevance=.99, history_overlap=1., supplement_score=0.),
                      dict(doc_key='new', relevance=.8, history_overlap=.3, supplement_score=.5)]
        self.assertEqual(_choose(candidates, [], True, False)[0][0], 'new')
        self.assertEqual(_choose(candidates, [], False, False)[0][0], 'covered')
        self.assertEqual(len(_choose(candidates, [], True, False)), 1)

    def test_missing_semantic_provider_explicitly_disables_legacy_supplement(self):
        legacy = {'items': [{'supplement_eligible': True, 'supplement_evidence': {'status': 'lexical_candidate'},
                             'goal_evidence': {'text': 'example'}}]}
        with patch('knowpipe.recommendations.lexical_baseline.recommend', return_value=legacy):
            result = recommend(None, None, 'database recovery', [{'source': 'test', 'doc_id': 'old'}])
        self.assertEqual(result['semantic']['status'], 'unavailable')
        self.assertFalse(result['items'][0]['supplement_eligible'])
        self.assertEqual(result['items'][0]['comparison']['status'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
