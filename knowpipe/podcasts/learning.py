"""Podcast full-text publication and evidence-gated personal notifications."""
from __future__ import annotations
import re
from pymongo.errors import DuplicateKeyError
from ..learning.content import content_view, publish_fulltext
from . import store


def transcript_language(text, declared=None):
    if declared and re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*', declared):
        return declared
    letters = [c for c in text if c.isalpha()]
    if letters and sum('\u4e00' <= c <= '\u9fff' for c in letters) / len(letters) > .3:
        return 'zh'
    # A Latin alphabet is not proof of English. Undeclared languages remain explicit.
    return 'und'


def publish_episode(db, episode, text, language, origin, token, claimed_at, check_lease):
    key={'source':'podcast','doc_id':episode['episode_id']}
    check_lease()
    # An older attempt cannot reclaim a document already reserved by a newer worker.
    try:
        reserved=db.documents.update_one({**key,'$or':[
            {'podcast_claimed_at':{'$exists':False}}, {'podcast_claimed_at':{'$lte':claimed_at}}]},
            {'$set':{'podcast_attempt':token,'podcast_claimed_at':claimed_at,
                     'title':episode['title'],'source_url':episode.get('source_url') or episode.get('audio_url') or '',
                     'feed_id':episode['feed_id'],'document_type':'podcast_transcript',
                     'audio_url':episode.get('audio_url'),'transcript_origin':origin,
                     'provenance':{'transcript_url':episode.get('transcript_url'),'retrieved_at':store.now(),
                                   'method':origin},'license':'Publisher terms; see original episode'}} ,upsert=True)
    except DuplicateKeyError:
        raise RuntimeError('worker_lease_lost') from None
    if not (reserved.matched_count or reserved.upserted_id):
        raise RuntimeError('worker_lease_lost')
    check_lease()
    doc=publish_fulltext(db,**key,text=text,language=language,expected={'podcast_attempt':token})
    if not doc:
        raise RuntimeError('worker_lease_lost')
    if origin=='asr':
        db.documents.update_one({**key,'podcast_attempt':token,'content.version':doc['content']['version']},
            {'$set':{'processing.transcription':{'status':'ready','source_version':doc['content']['version']}}})
    return doc


def publish_recommendations(db, job, result):
    """All RSS updates remain in episodes; only current, readable recommendations notify."""
    from ..recommendations.queue import is_current
    # A lexical fallback is useful for browsing, but is not enough evidence to
    # proactively notify. This also prevents legacy lexical supplement flags
    # from being mistaken for the new comparison policy.
    if (result.get('semantic') or {}).get('status') not in {'ready', 'partial'}:
        return 0
    runtime=db.recommendation_runtime.find_one({'_id':'worker'}) or {}
    corpus_id=(runtime.get('corpus') or {}).get('corpus_id')
    if not is_current(db,job,corpus_id):
        return 0
    # The complete set of history versions used for scoring must still match.
    for ref in result.get('history_references',[]):
        doc=db.documents.find_one({'source':ref['source'],'doc_id':ref['doc_id']}) or {}
        view=content_view(doc,include_text=False)
        if view['content_status']!='fulltext' or view['content_version']!=ref['content_version']:
            return 0
    published=0
    for item in result.get('items',[]):
        if item['source']!='podcast':
            continue
        if job.get('history') or result.get('history_relevant_paragraphs',0):
            if not item.get('supplement_eligible') or (item.get('comparison') or {}).get('status') != 'possible_supplement':
                continue
        episode=db.podcast_episodes.find_one({'episode_id':item['doc_id'],'status':'ready'})
        if not episode:
            continue
        subscription=db.podcast_subscriptions.find_one({'user_id':job['user_id'],'feed_id':episode['feed_id']})
        if not subscription or subscription['created_at'].replace(tzinfo=None)>episode['ready_at'].replace(tzinfo=None):
            continue
        doc=db.documents.find_one({'source':'podcast','doc_id':item['doc_id']}) or {}
        view=content_view(doc,include_text=False)
        if view['content_version']!=item['content_version'] or not view['chinese_ready']:
            continue
        if not is_current(db,job,corpus_id):
            break
        key={'user_id':job['user_id'],'episode_id':item['doc_id']}
        outcome=db.notifications.update_one(key,{'$set':{'kind':'learning_recommendation',
            'title':item['title'],'source':'podcast','doc_id':item['doc_id'],'feed_id':episode['feed_id'],
            'input_revision':job['revision'],'corpus_id':job['corpus_id'],'job_id':job['_id'],
            'processing_id':job.get('processing_id'),
            'semantic_processor_id':(result.get('semantic') or {}).get('processor_id'),
            'content_version':item['content_version'],
            'reason':item.get('reason'),'supplement_evidence':item.get('supplement_evidence'),
            'recommendation_mode':'supplement' if result.get('history_relevant_paragraphs',0) else 'goal_only'},
            '$setOnInsert':{**key,'created_at':store.now(),'read':False}},upsert=True)
        published+=bool(outcome.upserted_id)
    return published


def notification_current(db, notice):
    """Suppress stale personalized notifications after profile/content changes or unsubscribe."""
    if notice.get('kind')!='learning_recommendation':
        return True  # Historical notifications retain their original contract.
    runtime=db.recommendation_runtime.find_one({'_id':'worker'}) or {}
    if notice.get('processing_id') != (runtime.get('corpus') or {}).get('processing_id'):
        return False
    if not db.user_profiles.find_one({'user_id':notice['user_id'],'learning_revision':notice['input_revision']}):
        return False
    if not db.podcast_subscriptions.find_one({'user_id':notice['user_id'],'feed_id':notice['feed_id']}):
        return False
    job=db.recommendation_jobs.find_one({'_id':notice.get('job_id')}) or {}
    for ref in (job.get('result') or {}).get('history_references',[]):
        prior=db.documents.find_one({'source':ref['source'],'doc_id':ref['doc_id']}) or {}
        if content_view(prior,include_text=False)['content_version']!=ref['content_version']:
            return False
    doc=db.documents.find_one({'source':notice['source'],'doc_id':notice['doc_id']}) or {}
    view=content_view(doc,include_text=False)
    return view['chinese_ready'] and view['content_version']==notice['content_version']
