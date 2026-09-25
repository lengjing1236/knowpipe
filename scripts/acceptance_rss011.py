#!/usr/bin/env python3
"""Replay one intact publisher RSS item through real audio/ASR and current worker.

The frozen RSS response is declared; production code still fetches the public
complete audio and performs ASR. This is not long-running live-feed validation.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from scripts.acceptance_media import prepare_technical_audio, run_rss_stage
from knowpipe.learning.store import save_goal
from knowpipe.learning.content import content_view
from knowpipe.recommendations.worker import RecommendationWorker
from knowpipe.recommendations.importer import import_record
from knowpipe.recommendations import queue
from knowpipe.podcasts.learning import notification_current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default='mongodb://127.0.0.1:27018')
    parser.add_argument('--cache', default='state/feature009/media')
    parser.add_argument('--corpus', default='state/feature011/evaluation/selected-documents.jsonl')
    parser.add_argument('--output', default='evidence/011-mvp-recommendation-validation/rss-real.json')
    args = parser.parse_args()
    cache = Path(args.cache)
    report = {'status': 'running', 'scope': 'Explicit intact RSS item replay; actual public audio download, ASR and current semantic worker',
              'learning_quality_verified': False, 'notification_success_is_not_learning_effectiveness': True}
    started = time.monotonic()
    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    worker = None
    database_name = None
    try:
        source, _ = prepare_technical_audio(cache)
        transcript, stage = run_rss_stage(cache, args.mongo_uri, True)
        database_name = stage['database_name']; db = client[database_name]
        report.update(source=source, asr_stage=stage, transcript_characters=len(transcript.text), language=transcript.language)
        for line in Path(args.corpus).read_text().splitlines():
            import_record(db, json.loads(line))
        uid = db.podcast_subscriptions.find_one({})['user_id']
        goal = '学习 ECS Fargate 容器使用 tmpfs 内存文件系统的限制与适用场景'
        save_goal(db, uid, goal)
        worker = RecommendationWorker(db, 'state/feature011/rss-index')
        assert worker.run_once(refresh=True), 'no_production_job_processed'
        view = queue.view(db, uid)
        report['recommendations'] = view
        assert view['status'] in ('ready', 'empty'), view['status']
        selected = next((x for x in view['items'] if x['source'] == 'podcast' and x['doc_id'] == stage['episode_id']), None)
        notices = list(db.notifications.find({'user_id': uid, 'episode_id': stage['episode_id']}, {'_id': 0}))
        report['decision'] = ('not_selected' if selected is None else
                              'selected_chinese_ready' if selected['chinese_ready'] else 'selected_translation_unready')
        report['notifications'] = notices
        if notices:
            assert selected and selected['chinese_ready']
            assert len(notices) == 1 and notification_current(db, notices[0])
            assert notices[0]['recommendation_mode'] == 'goal_only'
            worker.run_once()
            assert db.notifications.count_documents({'user_id': uid, 'episode_id': stage['episode_id']}) == 1
        doc = db.documents.find_one({'source': 'podcast', 'doc_id': stage['episode_id']})
        assert content_view(doc)['content_status'] == 'fulltext'
        report.update(status='pipeline_completed', fulltext_version=doc['content']['version'],
                      history_scope='cold start; no claim of personal supplement',
                      index={k: worker.index.snapshot.get(k) for k in ('corpus_id', 'document_count', 'strategy')})
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error)[:300])
        raise
    finally:
        if worker: worker.close()
        report['seconds'] = time.monotonic() - started
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n')
        if database_name: client.drop_database(database_name)
        client.close()


if __name__ == '__main__':
    main()
