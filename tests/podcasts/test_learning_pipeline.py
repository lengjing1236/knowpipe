from datetime import timedelta
import mongomock
import pytest
from knowpipe.podcasts import store, worker
from knowpipe.learning.providers import TextResult, UnconfiguredProvider
from knowpipe.learning.content import content_view, publish_fulltext
from knowpipe.podcasts.learning import publish_recommendations

RSS = b'<rss><channel><language>en</language><item><guid>audio</guid><title>Redis persistence</title><enclosure url="https://example.org/episode.mp3"/></item></channel></rss>'


def test_audio_to_fulltext_idempotent_without_blanket_notifications():
    db = mongomock.MongoClient().test
    store.subscribe(db, 'a', 'https://example.org/feed')
    calls=[]
    def audio(url, provider):
        calls.append(url)
        return TextResult('Redis persistence saves snapshots. Recovery loads durable data.', 'en')
    worker.run_once(db, fetch=lambda _: (RSS, 'text/xml'), transcriber=UnconfiguredProvider(), audio_fetch=audio)
    worker.run_once(db, fetch=lambda _: (RSS, 'text/xml'), transcriber=UnconfiguredProvider(), audio_fetch=audio)
    assert len(calls)==1
    doc=db.documents.find_one({'source':'podcast'})
    assert content_view(doc)['content_status']=='fulltext'
    assert content_view(doc)['processing']['transcription']['status']=='ready'
    assert db.podcast_episodes.find_one({})['status']=='ready'
    assert db.notifications.count_documents({})==0


def test_partial_asr_never_publishes_fulltext():
    db=mongomock.MongoClient().test
    store.subscribe(db,'a','https://example.org/feed')
    worker.run_once(db,fetch=lambda _: (RSS,'text/xml'),audio_fetch=lambda *_: TextResult('part','en',False))
    assert db.podcast_episodes.find_one({})['status']=='failed'
    assert db.documents.count_documents({'content.kind':'fulltext'})==0


def setup_notice():
    db=mongomock.MongoClient().test
    store.ensure_indexes(db)
    feed=store.subscribe(db,'a','https://example.org/feed')
    db.podcast_episodes.insert_one({'episode_id':'e','feed_id':feed,'title':'Redis persistence','ready_at':store.now(),'status':'ready'})
    db.documents.insert_one({'source':'podcast','doc_id':'e'})
    doc=publish_fulltext(db,'podcast','e','Redis 持久化数据恢复原理。','zh')
    item={'source':'podcast','doc_id':'e','title':'Redis','content_version':doc['content']['version'],'reason':'goal_only','supplement_eligible':False}
    job={'_id':'j','user_id':'a','revision':1,'corpus_id':'c','history':[]}
    db.user_profiles.insert_one({'user_id':'a','learning_revision':1,'desired_recommendation_job':'j'})
    db.recommendation_runtime.insert_one({'_id':'worker','corpus':{'corpus_id':'c'}})
    return db,job,item,feed


def test_personal_notifications_version_subscription_and_chinese_gates():
    db,job,item,feed=setup_notice()
    result={'items':[item],'reason':'goal_only','history_relevant_paragraphs':0,'semantic':{'status':'ready','processor_id':'test-semantic'}}
    publish_recommendations(db,job,result)
    publish_recommendations(db,job,result)
    assert db.notifications.count_documents({})==1
    assert db.notifications.find_one({})['kind']=='learning_recommendation'
    db.notifications.delete_many({})
    db.user_profiles.update_one({'user_id':'a'},{'$set':{'learning_revision':2}})
    publish_recommendations(db,job,result)
    assert db.notifications.count_documents({})==0
    db.user_profiles.update_one({'user_id':'a'},{'$set':{'learning_revision':1}})
    store.unsubscribe(db,'a',feed)
    publish_recommendations(db,job,result)
    assert db.notifications.count_documents({})==0


def test_history_without_supplement_and_english_not_ready_do_not_notify():
    db,job,item,_=setup_notice()
    publish_recommendations(db,job,{'items':[item],'history_relevant_paragraphs':1})
    assert db.notifications.count_documents({})==0
    item['supplement_eligible']=True
    doc=publish_fulltext(db,'podcast','e','Redis persistence and recovery.','en')
    item['content_version']=doc['content']['version']
    publish_recommendations(db,job,{'items':[item],'history_relevant_paragraphs':1})
    assert db.notifications.count_documents({})==0


def test_unconfigured_asr_does_not_download_audio():
    db=mongomock.MongoClient().test
    store.subscribe(db,'a','https://example.org/feed')
    from unittest.mock import patch
    with patch('knowpipe.podcasts.audio.transcribe_url',side_effect=AssertionError('must not download')):
        worker.run_once(db,fetch=lambda _: (RSS,'text/xml'),transcriber=UnconfiguredProvider())
    episode=db.podcast_episodes.find_one({})
    assert episode['error_code']=='transcription_unavailable'
    assert db.documents.count_documents({})==0


def test_expired_attempt_cannot_overwrite_newer_document():
    from knowpipe.podcasts.learning import publish_episode
    db=mongomock.MongoClient().test
    store.ensure_indexes(db)
    episode={'episode_id':'one','feed_id':'f','title':'Title','source_url':'https://example.org'}
    now=store.now()
    publish_episode(db,episode,'Newer complete text.','en','publisher','new',now,lambda:None)
    with pytest.raises(RuntimeError,match='worker_lease_lost'):
        publish_episode(db,episode,'Old text.','en','publisher','old',now-timedelta(seconds=1),lambda:None)
    assert db.documents.find_one({})['body_text']=='Newer complete text.'


def test_having_only_unrelated_history_does_not_claim_supplement_in_notification():
    db,job,item,_=setup_notice()
    job['history']=[{'source':'docs','doc_id':'other','content_version':'v'}]
    publish_recommendations(db,job,{'items':[item],'history_relevant_paragraphs':0})
    assert db.notifications.count_documents({})==0


def test_late_publisher_transcript_recovers_failed_audio_episode():
    db=mongomock.MongoClient().test
    feed=store.subscribe(db,'a','https://example.org/feed')
    worker.run_once(db,fetch=lambda _: (RSS,'text/xml'),transcriber=UnconfiguredProvider())
    db.podcast_episodes.update_many({}, {'$set':{'attempts':3,'transcript':'old ASR text'}})
    db.podcast_feeds.update_one({'feed_id':feed},{'$set':{'next_poll_at':store.now()}})
    newer=RSS.replace(b'<rss>',b'<rss xmlns:podcast="https://podcastindex.org/namespace/1.0">').replace(
        b'</item>',b'<podcast:transcript url="https://example.org/transcript.txt" type="text/plain"/></item>')
    def fetch(url):
        return (newer,'text/xml') if url.endswith('/feed') else (b'Publisher full text about Redis persistence and recovery.','text/plain')
    worker.run_once(db,fetch=fetch)
    episode=db.podcast_episodes.find_one({})
    assert episode['status']=='ready'
    assert episode['transcript_origin']=='publisher'
    assert 'Publisher full text' in db.documents.find_one({})['body_text']


def test_processor_upgrade_refreshes_notice_evidence_without_notifying_twice():
    from knowpipe.podcasts.learning import notification_current
    db, job, item, _ = setup_notice()
    result = {'items': [item], 'history_relevant_paragraphs': 0, 'semantic': {'status': 'ready', 'processor_id': 'test-semantic'}}
    job['processing_id'] = 'old-processor'
    db.recommendation_runtime.update_one({}, {'$set': {'corpus.processing_id': 'old-processor'}})
    assert publish_recommendations(db, job, result) == 1
    original = db.notifications.find_one({})
    db.notifications.update_one({'_id': original['_id']}, {'$set': {'read': True}})

    db.recommendation_runtime.update_one({}, {'$set': {'corpus.processing_id': 'new-processor'}})
    assert not notification_current(db, db.notifications.find_one({}))
    job.update(_id='upgraded-job', processing_id='new-processor')
    db.user_profiles.update_one({}, {'$set': {'desired_recommendation_job': 'upgraded-job'}})
    assert publish_recommendations(db, job, result) == 0
    updated = db.notifications.find_one({})
    assert db.notifications.count_documents({}) == 1
    assert updated['_id'] == original['_id']
    assert updated['created_at'] == original['created_at']
    assert updated['read'] is True
    assert updated['processing_id'] == 'new-processor'
    assert updated['job_id'] == 'upgraded-job'
    assert notification_current(db, updated)

    # A late call from the superseded processor cannot restore stale evidence.
    stale = dict(job, _id='j', processing_id='old-processor')
    assert publish_recommendations(db, stale, result) == 0
    assert db.notifications.find_one({})['processing_id'] == 'new-processor'
