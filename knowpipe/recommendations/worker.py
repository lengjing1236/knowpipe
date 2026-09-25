"""Independent, single-active Spark recommendation worker (no Spark in Web)."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time

from ..learning.content import content_view
from ..learning.providers import UnconfiguredProvider, translate_document, translation_matches, processor_identity
from ..learning.local_providers import configured_translator, configured_goal_translator
from ..podcasts.learning import publish_recommendations
from datetime import timedelta
from . import queue
from .engine import recommend
from .index import prepare_snapshot, build_index, load_index, mark_indexed, _valid_artifacts
from .query import prepare_goal, processing_identity
from .runtime import create_spark

LOG = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, db, owner, job=None):
        self.db, self.owner, self.job = db, owner, job
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(15):
            try:
                renewed = queue.renew_job(self.db, self.job) if self.job else queue.renew_worker(self.db, self.owner)
                if not renewed:
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                LOG.error('recommendation_heartbeat_failed')
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=6)


class RecommendationWorker:
    def __init__(self, db, root, *, spark=None, translator=None, goal_translator=None, semantic=None):
        queue.ensure_indexes(db)
        self.db, self.root, self.spark = db, root, spark
        self.translator = translator or configured_translator()
        self.goal_translator = goal_translator or configured_goal_translator()
        from .semantic import configured_semantic, EnglishText
        self.semantic = semantic if semantic is not None else configured_semantic()
        if self.semantic is not None:
            self.semantic.text = EnglishText(self.goal_translator, db.semantic_translations)
        self.owner = None
        self.index = None
        self.last_refresh = 0.

    def _spark(self):
        if self.spark is None:
            self.spark = create_spark('knowpipe-learning-recommendations', self.root)
            self.spark.sparkContext.setLogLevel('WARN')
        return self.spark

    def refresh(self):
        with Heartbeat(self.db, self.owner) as beat:
            snapshot = prepare_snapshot(self.db, self.root)
            if self.index is None or self.index.snapshot['corpus_id'] != snapshot['corpus_id'] or not _valid_artifacts(self.index.snapshot):
                prior_runtime = self.db.recommendation_runtime.find_one({'_id': 'worker'}) or {}
                previous = self.index.snapshot if self.index else prior_runtime.get('corpus')
                build_index(self._spark(), snapshot, previous=previous)
                new_index = load_index(self.spark, snapshot)
                if self.index:
                    self.index.close()
                self.index = new_index
                mark_indexed(self.db, snapshot)
            else:
                snapshot.update(self.index.snapshot)
            if beat.lost.is_set():
                raise RuntimeError('worker_lease_lost')
            snapshot['processing_id'] = processing_identity(self.goal_translator, self.translator, self.semantic)
            runtime = queue.publish_corpus(self.db, self.owner, snapshot)
            if runtime is None:
                raise RuntimeError('worker_lease_lost')
            self.last_refresh = time.monotonic()
            return runtime

    def prepare_chinese(self, result, *, guard=None, generation=None):
        for item in result.get('items', []):
            if guard is not None and not guard():
                return
            doc = self.db.documents.find_one({'source': item['source'], 'doc_id': item['doc_id']})
            view = content_view(doc or {}, include_text=False)
            if view['content_status'] != 'fulltext' or view['content_version'] != item['content_version'] or translation_matches(doc, self.translator):
                continue
            if generation is not None and doc.get('translation_generation', -1) > generation:
                continue
            if isinstance(self.translator, UnconfiguredProvider) and view['processing']['translation']['status'] == 'unavailable':
                continue
            attempts = doc.get('translation_attempts') or {}
            processor_id = processor_identity(self.translator)
            if attempts.get('source_version') == item['content_version'] and attempts.get('processor_id') == processor_id:
                if attempts.get('count', 0) >= 3:
                    if view['processing']['translation']['status'] == 'running':
                        # This worker's global lease proves a previous execution is
                        # no longer active. Expose an exhausted interrupted attempt.
                        from ..learning.content import set_stage_state
                        set_stage_state(self.db, doc, 'translation', 'failed', 'translation_attempts_exhausted',
                                        expected_processor=processor_id, generation=doc.get('translation_generation'))
                    continue
                if attempts.get('retry_at', queue.utcnow()).replace(tzinfo=None) > queue.utcnow():
                    continue
            else:
                attempts = {'count': 0}
            changed = self.db.documents.update_one({'source': item['source'], 'doc_id': item['doc_id'],
                'content.version': item['content_version'], 'translation_generation': doc.get('translation_generation')},
                {'$set': {'translation_attempts': {
                    'source_version': item['content_version'], 'count': attempts.get('count', 0) + 1,
                    'processor_id': processor_id,
                    'retry_at': queue.utcnow() + timedelta(minutes=5)}}})
            if not changed.matched_count or (guard is not None and not guard()):
                continue
            translate_document(self.db, item['source'], item['doc_id'], self.translator, generation=generation)

    def _translation_guard(self, job, beat):
        return (not beat.lost.is_set() and bool(self.db.recommendation_runtime.find_one(
            queue._worker_filter(self.owner, queue.utcnow()))) and
            queue.is_current(self.db, job, self.index.snapshot['corpus_id']))

    def run_once(self, *, refresh=False):
        if self.owner is None:
            self.owner = queue.acquire_worker(self.db)
            self.last_refresh = 0.
        if self.owner is None:
            return False
        if not queue.renew_worker(self.db, self.owner):
            self.owner = None
            return False
        try:
            if refresh or self.index is None or time.monotonic() - self.last_refresh >= 60:
                runtime = self.refresh()
            else:
                runtime = self.db.recommendation_runtime.find_one({'_id': 'worker'})
            # Saved profiles are durable intent; HTTP/queue partial failures cannot lose work.
            for profile in self.db.user_profiles.find({'learning_goal.text': {'$type': 'string'}}):
                queue.schedule(self.db, profile, runtime)
        except Exception as error:
            limits = {'document_limit_exceeded', 'paragraph_limit_exceeded'}
            code = str(error) if isinstance(error, ValueError) and str(error) in limits else 'corpus_preparation_failed'
            self.db.recommendation_runtime.update_one(queue._worker_filter(self.owner, queue.utcnow()),
                {'$set': {'error_code': code}})
            LOG.exception('recommendation_corpus_preparation_failed')
            return False
        job = queue.claim(self.db, self.owner)
        if job is None:
            # A configured model can recover a previously unready translation without
            # recomputing Spark scores. Attempts are version-bound and bounded.
            for profile in self.db.user_profiles.find({'desired_recommendation_job': {'$exists': True}}):
                ready = self.db.recommendation_jobs.find_one({'_id': profile['desired_recommendation_job'], 'status': 'ready'})
                if ready and queue.is_current(self.db, ready, self.index.snapshot['corpus_id']):
                    with Heartbeat(self.db, self.owner) as beat:
                        self.prepare_chinese(ready['result'], guard=lambda: self._translation_guard(ready, beat),
                                             generation=runtime['generation'])
                        if not beat.lost.is_set():
                            publish_recommendations(self.db, ready, ready['result'])
            return False
        if not queue.is_current(self.db, job, self.index.snapshot['corpus_id']):
            self.db.recommendation_jobs.update_one(queue._attempt_filter(job, queue.utcnow()), {'$set': {'status': 'stale'}})
            return True
        completed = None
        with Heartbeat(self.db, self.owner, job) as beat:
            try:
                query_plan = prepare_goal(self.db, job['goal'], self.goal_translator)
                result = recommend(self.spark, self.index, job['goal'], job['history'], query_plan=query_plan,
                                   semantic=self.semantic)
                if beat.lost.is_set() or not queue.is_current(self.db, job, self.index.snapshot['corpus_id']):
                    self.db.recommendation_jobs.update_one(queue._attempt_filter(job, queue.utcnow()), {'$set': {'status': 'stale'}})
                    return True
                if (not beat.lost.is_set() and queue.is_current(self.db, job, self.index.snapshot['corpus_id'])
                        and queue.finish(self.db, job, result)):
                    completed = result
            except Exception as error:
                limits = {'history_limit_exceeded', 'goal_term_limit_exceeded', 'history_paragraph_limit_exceeded'}
                code = str(error) if isinstance(error, ValueError) and str(error) in limits else 'computation_failed'
                queue.fail(self.db, job, code)
                LOG.exception('recommendation_job_failed')
            finally:
                # Results are materialized before publication. Release the ONNX
                # sessions before the separate, potentially long full-text phase;
                # LocalSemantic lazily reloads them for the next recommendation.
                try:
                    close_semantic = getattr(self.semantic, 'close', None)
                    if callable(close_semantic):
                        close_semantic()
                except Exception as error:
                    # Cleanup must not replace a result or the original job
                    # failure. Do not log arbitrary provider exception text.
                    LOG.warning('recommendation_semantic_release_failed: %s', type(error).__name__)
        # Publish the computed list before full-text translation. Readers can see
        # the real processing state while every selected document is prepared.
        # The finished job no longer owns a renewable job lease; use worker lease
        # plus current profile/corpus guards during this recoverable phase.
        if completed is not None:
            with Heartbeat(self.db, self.owner) as beat:
                self.prepare_chinese(completed, guard=lambda: self._translation_guard(job, beat),
                                     generation=runtime['generation'])
                if not beat.lost.is_set():
                    publish_recommendations(self.db, job, completed)
        return True

    def close(self):
        if self.index:
            self.index.close()
        if self.spark:
            self.spark.stop()
        if self.owner:
            queue.release_worker(self.db, self.owner)


def main():
    from pymongo import MongoClient
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mongo-uri', default=os.environ.get('MONGO_URI', 'mongodb://localhost:27017'))
    parser.add_argument('--mongo-db', default=os.environ.get('MONGO_DB', 'knowpipe_mining'))
    parser.add_argument('--index-root', default=os.environ.get('LEARNING_INDEX_ROOT', 'state/recommendations'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        worker = RecommendationWorker(client[args.mongo_db], args.index_root)
        try:
            while True:
                worked = worker.run_once()
                if args.once:
                    break
                time.sleep(1 if worked else 5)
        except KeyboardInterrupt:
            pass
        finally:
            worker.close()


if __name__ == '__main__':
    main()
