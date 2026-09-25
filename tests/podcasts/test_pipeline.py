import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock

from knowpipe.podcasts import store, worker
from knowpipe.web.app import create_app

RSS = b'''<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Course podcast</title>
<item><guid>one</guid><title>Distributed data</title><podcast:transcript url="https://example.com/transcript.txt" type="text/plain"/></item>
<item><guid>two</guid><title>Audio only</title></item></channel></rss>'''


class PodcastPipelineTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient(tz_aware=True).test
        store.ensure_indexes(self.db)

    def test_poll_process_retry_and_notification_idempotency(self):
        feed_id = store.subscribe(self.db, 'u1', 'https://example.com/feed')
        store.subscribe(self.db, 'u2', 'https://example.com/feed')
        def fetch(url):
            return (RSS, 'application/rss+xml') if url.endswith('/feed') else (b'Spark distributed processing text.', 'text/plain')
        analyze = lambda text: {'segments': [{'segment_id': 0}], 'spark_application_id': 'test-app', 'spark_master': 'test', 'weighting': 'tfidf'}
        worker.run_once(self.db, fetch=fetch, analyze=analyze)
        worker.run_once(self.db, fetch=fetch, analyze=analyze)
        self.assertEqual(self.db.podcast_episodes.count_documents({}), 2)
        self.assertEqual(self.db.podcast_episodes.count_documents({'status': 'awaiting_transcript'}), 1)
        self.assertEqual(self.db.notifications.count_documents({}), 0)
        ready = self.db.podcast_episodes.find_one({'status': 'ready'})
        self.assertTrue(ready['batch_id'])
        self.assertEqual(self.db.batches.find_one({'batch_id': ready['batch_id']})['status'], 'success')
        store.unsubscribe(self.db, 'u1', feed_id)
        self.assertEqual(len(store.list_episodes(self.db, 'u1')), 0)
        self.assertEqual(len(store.list_episodes(self.db, 'u2')), 2)

    def test_failed_processing_is_recorded_and_can_be_retried(self):
        store.subscribe(self.db, 'u1', 'https://example.com/feed')
        def fetch(url):
            return (RSS, 'application/rss+xml') if url.endswith('/feed') else (b'hello Spark', 'text/plain')
        def fail(_):
            raise RuntimeError('private database credentials')
        worker.run_once(self.db, fetch=fetch, analyze=fail)
        episode = self.db.podcast_episodes.find_one({'status': 'failed'})
        self.assertEqual(episode['attempts'], 1)
        self.assertEqual(self.db.notifications.count_documents({}), 0)
        self.assertNotIn('private', str(episode))
        self.assertEqual(self.db.batches.find_one({})['status'], 'failed')
        self.db.podcast_episodes.update_one({'episode_id': episode['episode_id']}, {'$set': {'retry_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
        worker.run_once(self.db, fetch=fetch, analyze=lambda _: {'segments': [], 'spark_application_id': 'test', 'spark_master': 'test'})
        self.assertEqual(self.db.podcast_episodes.find_one({'episode_id': episode['episode_id']})['status'], 'ready')

    def test_lease_excludes_competing_worker(self):
        self.assertTrue(store.acquire_lease(self.db, 'a'))
        self.assertFalse(store.acquire_lease(self.db, 'b'))
        store.release_lease(self.db, 'a')
        self.assertTrue(store.acquire_lease(self.db, 'b'))

    def test_repoll_only_adds_new_episodes_and_updates_existing_transcripts(self):
        feed_id = store.subscribe(self.db, 'u1', 'https://example.com/feed')

        def rss(days, add_transcript=False):
            items = ''.join(f'<item><guid>{day}</guid><title>Day {day}</title>'
                            f'<pubDate>Wed, {day:02d} Sep 2026 10:00:00 GMT</pubDate>'
                            + ('<podcast:transcript url="https://example.com/t.txt" type="text/plain"/>'
                               if add_transcript and day == 14 else '') + '</item>' for day in days)
            return ('<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel>'
                    + items + '</channel></rss>').encode(), 'application/rss+xml'

        worker.poll_feeds(self.db, lambda _: rss(range(1, 17)), lambda: None)
        self.assertEqual(self.db.podcast_episodes.count_documents({}), 3)
        self.db.podcast_feeds.update_one({'feed_id': feed_id}, {'$set': {'next_poll_at': store.now()}})
        worker.poll_feeds(self.db, lambda _: rss(range(1, 18), True), lambda: None)
        self.assertEqual(self.db.podcast_episodes.count_documents({}), 4)
        self.assertEqual(self.db.podcast_episodes.find_one({'title': 'Day 14'})['status'], 'queued')
        self.assertEqual(self.db.podcast_episodes.find_one({'title': 'Day 17'})['status'], 'awaiting_transcript')

    def test_api_ownership_and_manual_transcript(self):
        app = create_app(db=self.db, config={'CSRF_ENABLED': False})
        clients = []
        for name in ('alice', 'bob'):
            c = app.test_client()
            data = {'username': name, 'password': 'password123'}
            c.post('/api/auth/register', json=data)
            c.post('/api/auth/login', json=data)
            clients.append(c)
        with patch('knowpipe.web.routes_podcasts.public_target'):
            response = clients[0].post('/api/podcasts/subscriptions', json={'url': 'https://example.com/feed'})
        self.assertEqual(response.status_code, 201)
        feed_id = response.json['feed_id']
        self.db.podcast_episodes.insert_one({'episode_id': 'episode1', 'feed_id': feed_id, 'status': 'awaiting_transcript', 'title': 'T'})
        self.assertEqual(clients[1].get('/api/podcasts/episodes/episode1').status_code, 404)
        self.assertEqual(clients[1].post('/api/podcasts/episodes/episode1/transcript', json={'text': 'hello ' * 20}).status_code, 404)
        self.assertEqual(clients[0].post('/api/podcasts/episodes/episode1/transcript', json={'text': 'hello ' * 20}).status_code, 202)
        self.assertEqual(app.test_client().get('/api/notifications/stream').status_code, 401)


if __name__ == '__main__':
    unittest.main()

class NotificationContractTests(unittest.TestCase):
    def test_sse_resume_and_read_are_user_scoped(self):
        db = mongomock.MongoClient().test
        app = create_app(db=db, config={'CSRF_ENABLED': False, 'SSE_DURATION_SECONDS': 0})
        c = app.test_client()
        data = {'username': 'alice', 'password': 'password123'}
        user_id = c.post('/api/auth/register', json=data).json['user_id']
        c.post('/api/auth/login', json=data)
        first = db.notifications.insert_one({'user_id': user_id, 'episode_id': 'a', 'title': 'first'}).inserted_id
        second = db.notifications.insert_one({'user_id': user_id, 'episode_id': 'b', 'title': 'second'}).inserted_id
        foreign = db.notifications.insert_one({'user_id': 'other', 'episode_id': 'secret', 'title': 'private'}).inserted_id
        response = c.get('/api/notifications/stream', headers={'Last-Event-ID': str(first)})
        content = response.get_data(as_text=True)
        self.assertIn('second', content)
        self.assertNotIn('first', content)
        self.assertNotIn('private', content)
        self.assertEqual(c.post(f'/api/notifications/{foreign}/read', json={}).status_code, 404)
        self.assertEqual(c.post(f'/api/notifications/{second}/read', json={}).status_code, 200)
        self.assertTrue(db.notifications.find_one({'_id': second})['read'])

    def test_expired_lease_recovers_processing_and_does_not_duplicate_notifications(self):
        db = mongomock.MongoClient(tz_aware=True).test
        store.ensure_indexes(db)
        feed_id = store.subscribe(db, 'u1', 'https://example.com/feed')
        db.podcast_feeds.update_one({'feed_id': feed_id}, {'$set': {'next_poll_at': store.now() + timedelta(hours=1)}})
        db.podcast_episodes.insert_one({'episode_id': 'e', 'feed_id': feed_id, 'title': 'Recovered',
                                       'status': 'processing', 'batch_id': 'old', 'attempts': 1,
                                       'transcript': 'valid text', 'retry_at': store.now()})
        db.batches.insert_one({'batch_id': 'old', 'status': 'running'})
        store.acquire_lease(db, 'dead')
        db.podcast_worker_leases.update_one({'_id': 'podcast-worker'}, {'$set': {'expires_at': store.now() - timedelta(seconds=1)}})
        worker.run_once(db, analyze=lambda _: {'segments': [], 'spark_application_id': 'test', 'spark_master': 'test'})
        self.assertEqual(db.batches.find_one({'batch_id': 'old'})['status'], 'failed')
        self.assertEqual(db.podcast_episodes.find_one({'episode_id': 'e'})['status'], 'ready')
        self.assertEqual(db.notifications.count_documents({}), 0)

class SubscriptionQuotaTests(unittest.TestCase):
    def test_cap_duplicate_and_release(self):
        db = mongomock.MongoClient().test
        store.ensure_indexes(db)
        first = None
        for i in range(20):
            feed_id = store.subscribe(db, 'u1', f'https://example.com/feed/{i}')
            if i == 0:
                first = feed_id
        self.assertEqual(store.subscribe(db, 'u1', 'https://example.com/feed/0'), first)
        with self.assertRaisesRegex(ValueError, 'subscription_limit'):
            store.subscribe(db, 'u1', 'https://example.com/feed/21')
        store.unsubscribe(db, 'u1', first)
        store.subscribe(db, 'u1', 'https://example.com/feed/21')
        self.assertEqual(db.podcast_subscriptions.count_documents({'user_id': 'u1'}), 20)
