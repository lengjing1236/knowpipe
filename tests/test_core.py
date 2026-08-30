import os
import tempfile
import time
import unittest
from unittest import mock

from knowpipe.brain import Brain, BrainError
from knowpipe.cli import _load_batch_checkpoint, _save_batch_checkpoint, run_pipeline
from knowpipe.report import build_article_report
from knowpipe.store import MemoryStore, SQLiteMemoryStore, open_store, migrate_jsonl_to_sqlite
from knowpipe import podcast
from knowpipe import watch
from knowpipe.watch_store import WatchStore


class CoreBehaviorTests(unittest.TestCase):
    def test_article_report_does_not_include_transcripts(self):
        report = build_article_report(
            "标题", "source", "RAW TRANSCRIPT", "CLEAN TRANSCRIPT", "正文", "manual"
        )
        self.assertNotIn("RAW TRANSCRIPT", report)
        self.assertNotIn("CLEAN TRANSCRIPT", report)
        self.assertIn("正文", report)

    def test_checkpoint_only_resumes_matching_successful_pages(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "job.json")
            rows = [
                {"page": 1, "error": None},
                {"page": 2, "error": "temporary failure"},
            ]
            _save_batch_checkpoint(path, "BVtest", [1, 2], "heuristic", rows)
            resumed = _load_batch_checkpoint(path, "BVtest", [1, 2], "heuristic")
            self.assertEqual([row["page"] for row in resumed], [1])
            self.assertEqual(
                _load_batch_checkpoint(path, "BVtest", [1, 2], "openai"), []
            )
            _save_batch_checkpoint(
                path, "BVtest", [1, 2], "heuristic", rows,
                memory_path=os.path.join(root, "memory.jsonl"), mode="integrated",
                model="gpt-test"
            )
            self.assertEqual(
                _load_batch_checkpoint(
                    path, "BVtest", [1, 2], "heuristic",
                    memory_path=os.path.join(root, "other.jsonl"),
                    mode="integrated", model="gpt-test"
                ), []
            )

    def test_memory_index_is_invalidated_after_new_card(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryStore(os.path.join(root, "cards.jsonl"))
            store.add_new("事件循环调度任务", "test")
            store.candidates_for("事件循环")
            self.assertIsNotNone(store._index)
            store.add_new("新的向量检索知识", "test", persist=False)
            self.assertIsNone(store._index)
            store.save()

    def test_invalid_llm_verdict_is_rejected(self):
        with self.assertRaises(BrainError):
            Brain._normalize_verdicts(
                [{"index": 0, "verdict": "invalid", "confidence": 2}], 1
            )

    def test_heuristic_memory_answer_is_traceable(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryStore(os.path.join(root, "cards.jsonl"))
            card = store.add_new("事件循环负责调度任务", "test", persist=False)
            answer = Brain("heuristic").answer_question(
                "事件循环做什么？", store.candidates_for("事件循环")
            )
            self.assertIn(card["id"], answer)
            self.assertIn(card["claim"], answer)

    def test_openai_memory_answer_receives_only_retrieved_cards(self):
        brain = Brain("openai", api_key="test")
        calls = []

        def fake_chat(system, user, **kwargs):
            calls.append((system, user))
            return "依据 [c_demo]：事件循环负责调度任务。"

        brain._chat = fake_chat
        answer = brain.answer_question(
            "事件循环做什么？",
            [({"id": "c_demo", "claim": "事件循环负责调度任务", "topic": "Python", "status": "learned", "source": "test"}, 0.8)],
        )
        self.assertIn("[c_demo]", answer)
        self.assertIn("事件循环负责调度任务", calls[0][1])

    def test_llm_cache_avoids_repeated_request(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"KNOWPIPE_LLM_CACHE_DIR": root, "KNOWPIPE_LLM_CACHE": "1"}
        ):
            calls = []

            def fake_post(*args, **kwargs):
                calls.append(1)
                return {"choices": [{"message": {"content": "缓存答案"}}]}

            with mock.patch("knowpipe.brain._post_json", side_effect=fake_post):
                brain = Brain("openai", api_key="test", model="gpt-test")
                self.assertEqual(brain._chat("系统", "用户"), "缓存答案")
                self.assertEqual(brain._chat("系统", "用户"), "缓存答案")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(os.listdir(root)), 1)

    def test_offline_digest_does_not_fallback_to_raw_input(self):
        with tempfile.TemporaryDirectory() as root:
            raw = "这是不应出现在摘要回退中的 ASR 原文内容，长度足够。"
            result = run_pipeline(
                raw, "podcast:test", "节目", "episode-1",
                Brain("heuristic"), MemoryStore(os.path.join(root, "cards.jsonl"))
            )
            self.assertNotIn(raw, result["digest"])

    def test_podcast_feed_parses_transcript_and_episode(self):
        xml = """<?xml version='1.0'?>
        <rss xmlns:podcast='https://podcastindex.org/namespace/1.0'>
          <channel><title>示例节目</title>
            <item><title>第一集</title><guid>ep-1</guid>
              <enclosure url='audio/one.mp3' type='audio/mpeg'/>
              <podcast:transcript url='captions/one.vtt' type='text/vtt'/>
            </item>
          </channel>
        </rss>""".encode()
        original_get = podcast._http_get
        try:
            podcast._http_get = lambda url, timeout=30: (
                xml if url.endswith("feed.xml")
                else "WEBVTT\n\n00:00.000 --> 00:01.000\n你好，世界。".encode()
            )
            with tempfile.TemporaryDirectory() as root:
                result = podcast.fetch_episode(
                    "https://example.com/feed.xml", transcriber="transcript",
                    transcript_cache_dir=root
                )
        finally:
            podcast._http_get = original_get
        self.assertEqual(result["title"], "第一集")
        self.assertEqual(result["method"], "transcript")
        self.assertEqual(result["text"], "你好，世界。")

    def test_podcast_transcript_cache_avoids_second_download(self):
        xml = "<rss><channel><title>节目</title><item><title>一</title>"
        xml += "<guid>ep</guid><podcast:transcript xmlns:podcast='x' url='t.txt'/>"
        xml = (xml + "</item></channel></rss>").encode()
        with tempfile.TemporaryDirectory() as root:
            calls = []
            original_get = podcast._http_get

            def fake_get(url, timeout=30):
                calls.append(url)
                return xml if url.endswith("feed.xml") else "缓存测试文本".encode()

            try:
                podcast._http_get = fake_get
                first = podcast.fetch_episode(
                    "https://example.com/feed.xml", transcriber="transcript",
                    transcript_cache_dir=root
                )
                second = podcast.fetch_episode(
                    "https://example.com/feed.xml", transcriber="transcript",
                    transcript_cache_dir=root
                )
            finally:
                podcast._http_get = original_get
            self.assertEqual(first["text"], second["text"])
            self.assertEqual(second["method"], "cache")
            self.assertEqual(sum(url.endswith("t.txt") for url in calls), 1)

    def test_sqlite_store_round_trip_and_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            jsonl = os.path.join(root, "cards.jsonl")
            db = os.path.join(root, "cards.db")
            # seed 文件允许省略运行时生成的 id/时间字段。
            with open(jsonl, "w", encoding="utf-8") as f:
                f.write('{"claim":"协程可以暂停后恢复","source":"test","topic":"Python"}\n')
            self.assertEqual(migrate_jsonl_to_sqlite(jsonl, db), 1)
            store = open_store(db)
            self.assertIsInstance(store, SQLiteMemoryStore)
            self.assertEqual(store.stats()["total"], 1)
            store.update_status(store.cards[0]["id"], "learned")
            reopened = SQLiteMemoryStore(db)
            self.assertEqual(reopened.cards[0]["status"], "learned")
            self.assertTrue(reopened.delete(reopened.cards[0]["id"]))
            self.assertEqual(SQLiteMemoryStore(db).stats()["total"], 0)
            store.close()
            reopened.close()

    def test_watch_processes_guid_once_and_archives_report(self):
        feed_url = "https://example.com/podcast.xml"
        feed = {
            "url": feed_url,
            "title": "示例节目",
            "episodes": [{
                "guid": "episode-guid-1", "title": "第一集",
                "published": "2026-08-30", "link": "", "audio_url": "",
                "transcripts": [],
            }],
        }
        with tempfile.TemporaryDirectory() as root:
            calls = []
            with mock.patch.object(watch.podcast, "fetch_feed", return_value=feed), \
                    mock.patch.object(
                        watch.podcast, "fetch_episode_item",
                        return_value={"text": "这是节目正文。", "method": "transcript"},
                    ) as fetch_item, \
                    mock.patch.object(watch, "send_notifications", return_value=[]) as notify:
                first = watch.watch_once(
                    [feed_url], memory_path=os.path.join(root, "cards.jsonl"),
                    state_path=os.path.join(root, "state.db"),
                    archive_dir=os.path.join(root, "archive"), mode="article",
                    brain=Brain("heuristic"),
                )
                second = watch.watch_once(
                    [feed_url], memory_path=os.path.join(root, "cards.jsonl"),
                    state_path=os.path.join(root, "state.db"),
                    archive_dir=os.path.join(root, "archive"), mode="article",
                    brain=Brain("heuristic"),
                )
                self.assertEqual(len(first), 1)
                self.assertEqual(second, [])
                self.assertEqual(fetch_item.call_count, 1)
                self.assertEqual(notify.call_count, 1)
                report_path = first[0]["report_path"]
                self.assertTrue(os.path.exists(report_path))
                with open(report_path, encoding="utf-8") as report:
                    content = report.read()
                self.assertIn("知识总结", content)
                self.assertNotIn("这是节目正文。", content)

    def test_watch_store_claim_is_atomic_and_success_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            state = WatchStore(os.path.join(root, "watch.db"))
            item = {"guid": "g1", "title": "一集", "transcripts": []}
            state.upsert_feed("feed", "节目")
            state.discover_episode("feed", item)
            self.assertTrue(state.claim_episode("feed", "g1"))
            self.assertFalse(state.claim_episode("feed", "g1", stale_after=10**9))
            state.mark_success("feed", "g1", "/tmp/report.md")
            self.assertFalse(state.claim_episode("feed", "g1"))
            self.assertEqual(state.get_episode("feed", "g1")["status"], "success")
            state.close()

    def test_long_transcript_uses_hierarchical_summary(self):
        transcript = "这是一个需要分层处理的长节目内容。" * 500
        with mock.patch.dict(os.environ, {
            "KNOWPIPE_SUMMARY_SINGLE_LIMIT": "1000",
            "KNOWPIPE_SUMMARY_CHUNK_CHARS": "4000",
        }):
            brain = Brain("openai", api_key="test")
            calls = []

            def fake_chat(system, user, **kwargs):
                calls.append((system, user))
                if "总编辑" in system:
                    return "## 主题总结\n\n## 核心要点\n\n- 关键结论"
                return "片段编辑笔记：保留事实、限制和因果关系。"

            brain._chat = fake_chat
            article = brain.generate_article_from_transcript(transcript)
            self.assertIn("## 核心要点", article)
            self.assertGreater(len(calls), 2)  # 多个片段调用 + 最终总编辑
            self.assertTrue(all(len(user) <= 4500 for _, user in calls[:-1]))

    def test_whisper_audio_is_removed_after_transcription(self):
        with tempfile.TemporaryDirectory() as root:
            audio = os.path.join(root, "episode.mp3")
            with open(audio, "wb") as handle:
                handle.write(b"audio")
            item = {"guid": "audio-1", "title": "音频集", "audio_url": "https://x/audio.mp3",
                    "transcripts": []}
            with mock.patch.object(podcast, "download_audio", return_value=audio), \
                    mock.patch.object(podcast, "transcribe_local", return_value="转写内容"):
                result = podcast.fetch_episode_item(
                    "https://x/feed.xml", item, transcriber="whisper",
                    transcript_cache_dir=root,
                )
            self.assertEqual(result["text"], "转写内容")
            self.assertFalse(os.path.exists(audio))

    def test_transcript_cache_cleanup_removes_only_expired_files(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.path.join(root, "old.txt")
            fresh = os.path.join(root, "fresh.txt")
            other = os.path.join(root, "keep.json")
            for path in (old, fresh, other):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            old_time = time.time() - 3 * 86400
            os.utime(old, (old_time, old_time))
            self.assertEqual(podcast.cleanup_transcript_cache(root, retention_days=1), 1)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(fresh))
            self.assertTrue(os.path.exists(other))


if __name__ == "__main__":
    unittest.main()
