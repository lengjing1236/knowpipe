import mongomock
from knowpipe.learning.quality import CHECKS_VERSION
from knowpipe.learning.content import publish_fulltext, claim_translation, publish_translation
from knowpipe.recommendations.presentation import localize_evidence


def test_localization_uses_verified_version_and_actual_alignment():
    db = mongomock.MongoClient().db
    db.documents.insert_one({'source': 'docs', 'doc_id': 'd'})
    doc = publish_fulltext(db, 'docs', 'd', 'Use a transaction.', 'en')
    version = doc['content']['version']
    claim_translation(db, doc, 'translator-v1')
    publish_translation(db, 'docs', 'd', version, '使用事务。', processor_id='translator-v1',
        quality={'status': 'checks_passed', 'checks_version': CHECKS_VERSION}, segments=[{'source_start': 0, 'source_end': 18,
        'target_start': 0, 'target_end': 5, 'kind': 'text'}])
    ref = {'source': 'docs', 'doc_id': 'd', 'content_version': version}
    evidence = {'start': 6, 'end': 17, 'text': 'transaction'}
    localized = localize_evidence(db, evidence, ref, {})
    assert localized['chinese_context'] == '使用事务。'
    assert localized['chinese_context_scope'] == 'aligned_translation_units'
    assert evidence == {'start': 6, 'end': 17, 'text': 'transaction'}
    assert 'chinese_context' not in localize_evidence(db, evidence, {**ref, 'content_version': 'old'}, {})
    assert 'chinese_context' not in localize_evidence(db, {**evidence, 'text': 'invented'}, ref, {})


def test_legacy_translation_without_alignment_does_not_invent_quote():
    db = mongomock.MongoClient().db
    db.documents.insert_one({'source': 'docs', 'doc_id': 'd'})
    doc = publish_fulltext(db, 'docs', 'd', 'Use a transaction.', 'en')
    version = doc['content']['version']
    claim_translation(db, doc, 'legacy')
    publish_translation(db, 'docs', 'd', version, '使用事务。', processor_id='legacy', quality={'status': 'checks_passed', 'checks_version': CHECKS_VERSION})
    result = localize_evidence(db, {'start': 0, 'end': 18, 'text': 'Use a transaction.'},
                              {'source': 'docs', 'doc_id': 'd', 'content_version': version}, {})
    assert 'chinese_context' not in result
