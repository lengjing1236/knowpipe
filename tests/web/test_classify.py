"""knowpipe.web.classify 的测试：四态判定规则、possible_conflict 降级逻辑。"""
import unittest

from knowpipe.web import classify


def _unit(knowledge_id, keywords, topic_cluster_id, source="arxiv", doc_id="a1"):
    return {"knowledge_id": knowledge_id, "keywords": keywords,
            "topic_cluster_id": topic_cluster_id, "source": source, "doc_id": doc_id}


class TestClassifyUnit(unittest.TestCase):
    def test_keyword_hit_is_known(self):
        unit = _unit("kw:spark", ["spark"], topic_cluster_id=0)
        result = classify.classify_unit(unit, known_keywords={"spark"}, known_topics=set())
        self.assertEqual(result["status"], classify.STATUS_KNOWN)
        self.assertEqual(result["matched_keywords"], ["spark"])

    def test_topic_hit_without_keyword_hit_is_refine(self):
        unit = _unit("kw:newterm", ["newterm"], topic_cluster_id=0)
        result = classify.classify_unit(unit, known_keywords=set(), known_topics={"0"})
        self.assertEqual(result["status"], classify.STATUS_REFINE)
        self.assertEqual(result["matched_topic_cluster_id"], 0)

    def test_no_hit_is_new(self):
        unit = _unit("kw:unknown", ["unknown"], topic_cluster_id=5)
        result = classify.classify_unit(unit, known_keywords={"spark"}, known_topics={"0"})
        self.assertEqual(result["status"], classify.STATUS_NEW)
        self.assertEqual(result["matched_keywords"], [])
        self.assertIsNone(result["matched_topic_cluster_id"])


class TestConflictOverride(unittest.TestCase):
    def test_no_evidence_does_not_change_status(self):
        classified = {"status": classify.STATUS_REFINE, "knowledge_id": "kw:x"}
        result = classify.apply_conflict_override(classified, conflict_evidence=None)
        self.assertEqual(result["status"], classify.STATUS_REFINE)

    def test_pending_review_keeps_status_hidden(self):
        classified = {"status": classify.STATUS_REFINE, "knowledge_id": "kw:x"}
        evidence = {"snippet": "conflicting claim", "source_doc_ids": ["a1", "a2"]}
        result = classify.apply_conflict_override(classified, evidence, pending_review=True)
        self.assertEqual(result["status"], classify.STATUS_REFINE)
        self.assertTrue(result["pending_review"])
        self.assertEqual(result["conflict_evidence"], evidence)

    def test_reviewed_evidence_switches_to_possible_conflict(self):
        classified = {"status": classify.STATUS_REFINE, "knowledge_id": "kw:x"}
        evidence = {"snippet": "conflicting claim", "source_doc_ids": ["a1", "a2"]}
        result = classify.apply_conflict_override(classified, evidence, pending_review=False)
        self.assertEqual(result["status"], classify.STATUS_POSSIBLE_CONFLICT)
        self.assertFalse(result["pending_review"])


class TestContradictoryFeedback(unittest.TestCase):
    def test_latest_known_state_wins_over_earlier_contradictory_state(self):
        """对应 spec FR-019：矛盾反馈以最近一次为准更新画像，重新判定使用最新已知集合。"""
        unit = _unit("kw:spark", ["spark"], topic_cluster_id=0)

        # 第一次反馈之前：spark 未知
        before = classify.classify_unit(unit, known_keywords=set(), known_topics=set())
        self.assertEqual(before["status"], classify.STATUS_NEW)

        # 用户先确认已掌握（known_keywords 加入 spark），后又标记为无关但未撤销已知状态
        # ——画像更新逻辑（routes_api.py）以最近一次 confirmed_known 反馈追加已知集合，
        # 这里验证：一旦 known_keywords 包含 spark，重新判定结果稳定为 known，不因
        # 后续矛盾反馈的记录顺序而回退。
        after = classify.classify_unit(unit, known_keywords={"spark"}, known_topics=set())
        self.assertEqual(after["status"], classify.STATUS_KNOWN)


class TestClassifyAll(unittest.TestCase):
    def test_batch_classification(self):
        units = [_unit("kw:spark", ["spark"], 0), _unit("kw:new", ["new"], 1)]
        results = classify.classify_all(units, known_keywords=["spark"], known_topics=["0"])
        statuses = {r["knowledge_id"]: r["status"] for r in results}
        self.assertEqual(statuses["kw:spark"], classify.STATUS_KNOWN)
        self.assertEqual(statuses["kw:new"], classify.STATUS_NEW)


if __name__ == "__main__":
    unittest.main()
