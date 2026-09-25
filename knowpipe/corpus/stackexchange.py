"""Full Q&A threads using the official API; never count answers as documents."""
import html
from urllib.parse import urlencode

from .html import extract_html

ADAPTER_VERSION = 'stackexchange-complete-thread-v1'
FILTER = '!20aKG._8Oscv*6djs8Pgm'
LICENSE_POLICY = 'https://stackoverflow.com/help/licensing'
DOMAINS = {'stackoverflow': '编程与开发', 'dba': '数据库', 'unix': 'Unix与Linux',
           'serverfault': '系统与网络运维', 'softwareengineering': '软件工程', 'cs': '计算机理论'}
HOSTS = {'stackoverflow': 'stackoverflow.com', 'serverfault': 'serverfault.com',
         **{s: s + '.stackexchange.com' for s in ('dba', 'unix', 'softwareengineering', 'cs')}}


class CorpusRejected(ValueError):
    pass


def page_url(site, page):
    if site not in DOMAINS or not 1 <= page <= 25:
        raise ValueError('unsupported_site_or_anonymous_page')
    params = {'site': site, 'sort': 'creation', 'order': 'desc', 'accepted': 'true', 'closed': 'false',
              'migrated': 'false', 'pagesize': 100, 'page': page, 'filter': FILTER}
    return 'https://api.stackexchange.com/2.3/search/advanced?' + urlencode(params)


def _credit(post, site, post_id, role):
    owner = post.get('owner') or {}
    return {'name': html.unescape(owner.get('display_name') or 'Deleted or unavailable author'),
            'url': owner.get('link'), 'post_id': str(post_id), 'role': role,
            'post_url': 'https://' + HOSTS[site] + '/a/' + str(post_id) if role == 'answer' else 'https://' + HOSTS[site] + '/questions/' + str(post_id),
            'license': post.get('content_license') or 'CC BY-SA; version not returned by API',
            'license_policy_url': LICENSE_POLICY,
            'created_at_epoch': post.get('creation_date'), 'last_edit_at_epoch': post.get('last_edit_date')}


def question_record(question, site, provenance):
    if site not in DOMAINS:
        raise CorpusRejected('non_computing_site')
    if question.get('score', 0) < 0 or question.get('closed_date') or question.get('migrated_to'):
        raise CorpusRejected('negative_closed_or_migrated')
    qid = question.get('question_id')
    answers = question.get('answers')
    if not isinstance(qid, int) or qid <= 0 or not isinstance(answers, list) or not answers:
        raise CorpusRejected('missing_question_or_answers')
    if len(answers) != question.get('answer_count'):
        raise CorpusRejected('incomplete_answer_collection')
    aids = [answer.get('answer_id') for answer in answers]
    if len(set(aids)) != len(aids) or any(not isinstance(aid, int) or aid <= 0 for aid in aids):
        raise CorpusRejected('invalid_answer_identity')
    if question.get('accepted_answer_id') not in aids:
        raise CorpusRejected('accepted_answer_missing')
    title = html.unescape(question.get('title') or '').strip()
    if not title or not question.get('body') or not question.get('link'):
        raise CorpusRejected('missing_question_body')
    qtext, images = extract_html(question['body'], base_url=question['link'])
    if not qtext:
        raise CorpusRejected('empty_question_text')
    parts = [title, 'Question', qtext]
    credits = [_credit(question, site, qid, 'question')]
    ordered = sorted(answers, key=lambda a: (a['answer_id'] != question['accepted_answer_id'], -a.get('score', 0), a['answer_id']))
    for position, answer in enumerate(ordered, 1):
        if answer.get('question_id') != qid or not answer.get('body'):
            raise CorpusRejected('missing_or_mismatched_answer_body')
        text, answer_images = extract_html(answer['body'], base_url=question['link'])
        if not text:
            raise CorpusRejected('empty_answer_text')
        parts.extend(['Answer ' + str(position) + (' (accepted)' if answer['answer_id'] == question['accepted_answer_id'] else ''), text])
        images.extend(answer_images)
        credits.append(_credit(answer, site, answer['answer_id'], 'answer'))
    body = '\n\n'.join(parts)
    if len(body.encode('utf-8')) > 4_000_000:
        raise CorpusRejected('fulltext_exceeds_document_limit')
    return {'source': 'stackexchange', 'source_site': site, 'doc_id': site + '-' + str(qid),
            'title': title, 'body_text': body, 'language': 'en', 'source_url': question['link'],
            'license': 'CC BY-SA; per-post versions in authors; ' + LICENSE_POLICY,
            'fulltext_verified': True, 'document_type': 'qa_thread', 'tags': question.get('tags') or [],
            'authors': credits, 'topic_domain': DOMAINS[site],
            'quality': {'answers_expected': question['answer_count'], 'answers_received': len(answers),
                        'question_score': question.get('score'), 'accepted_answer_id': question['accepted_answer_id'],
                        'contains_media': bool(images), 'media_urls': list(dict.fromkeys(images)),
                        'media_content_extracted': False, 'comments_included': False,
                        'extraction_method': 'complete_question_and_all_answers_html',
                        'created_at_epoch': question.get('creation_date'), 'last_activity_epoch': question.get('last_activity_date')},
            'provenance': {**provenance, 'adapter_version': ADAPTER_VERSION, 'original_provider': 'Stack Exchange',
                           'selection': 'newest accepted, open, non-migrated questions; exclude negative question scores'}}
