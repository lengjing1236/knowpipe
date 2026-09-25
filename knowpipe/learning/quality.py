"""Conservative integrity checks, never a certificate of translation meaning.

Arabic numerals and Markdown code must survive translation. Written-out numbers
can conservatively fail this check. Passing cannot detect changed relationships
such as 'by 20%' becoming 'to 20%', mistranslated names, or omitted prose.
"""
from collections import Counter
import re
import unicodedata

CHECKS_VERSION = 'translation-integrity-v2'
ISSUES = {'number_missing', 'number_added', 'code_missing', 'identifier_missing', 'unknown_symbol'}


def translation_spans(text):
    """Yield (translate, text), preserving fenced/indented and inline code exactly."""
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            yield False, line
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if marker:
            fence = marker[1]
            yield False, line
            continue
        if line.startswith(('    ', '\t')) or not line.strip():
            yield False, line
            continue
        position = 0
        for match in re.finditer(r'(`+).*?\1', line):
            if match.start() > position:
                yield True, line[position:match.start()]
            yield False, match[0]
            position = match.end()
        if position < len(line):
            yield True, line[position:]


def _numbers(text):
    normalized = unicodedata.normalize('NFKC', text)
    matches = re.findall(r'(?<![A-Za-z0-9_])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*%)?', normalized)
    return Counter(re.sub(r'[,\s]', '', value) for value in matches)


def _code(text):
    return Counter(span for translate, span in translation_spans(text) if not translate and span.strip())


def check_translation(original, translated):
    issues = []
    if _numbers(original) - _numbers(translated):
        issues.append('number_missing')
    if _numbers(translated) - _numbers(original):
        issues.append('number_added')
    if _code(original) - _code(translated):
        issues.append('code_missing')
    if _identifiers(original) - _identifiers(translated):
        issues.append('identifier_missing')
    if any(translated.count(marker) > original.count(marker) for marker in ('\ufffd', '\u2047')):
        issues.append('unknown_symbol')
    return {'checks_version': CHECKS_VERSION, 'status': 'failed' if issues else 'checks_passed',
            'issues': issues, 'human_reviewed': False, 'semantic_verified': False}


def _identifiers(text):
    """Recognize syntax, not a list of acceptance-specific technical terms.

    This intentionally cannot recognize plain words such as 'fsync' as code when
    an extractor has discarded their markup. It is an integrity gate, not NER.
    """
    pattern = (r'(?<![A-Za-z0-9_])(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+'
               r'|[a-z_][a-z0-9_]+(?:\.[a-z_][a-z0-9_]+)+'
               r'|[A-Za-z_][A-Za-z0-9_]*\(\)'
               r'|[a-z]+(?:[A-Z][a-z0-9]*)+'
               r'|[A-Z][a-z]+[A-Z][A-Za-z0-9]*)'
               r'(?![A-Za-z0-9_])')
    return Counter(re.findall(pattern, text))


def validate_segments(original, translated, segments):
    """Validate actual character-offset alignment; never infer it from paragraphs.

    Offsets count Python Unicode code points, not UTF-8 bytes or JS UTF-16 units.
    Empty means this provider did not supply alignment, preserving old adapters.
    """
    if not isinstance(segments, (list, tuple)) or len(segments) > 100_000:
        raise ValueError('invalid_translation_alignment')
    if not segments:
        return []
    source_end = target_end = 0
    output = []
    fields = ('source_start', 'source_end', 'target_start', 'target_end')
    for segment in segments:
        if not isinstance(segment, dict) or any(type(segment.get(key)) is not int for key in fields):
            raise ValueError('invalid_translation_alignment')
        a, b, c, d = (segment[key] for key in fields)
        kind = segment.get('kind')
        if (a != source_end or c != target_end or not a < b <= len(original)
                or not c < d <= len(translated) or kind not in {'text', 'protected'}):
            raise ValueError('invalid_translation_alignment')
        if kind == 'protected' and original[a:b] != translated[c:d]:
            raise ValueError('invalid_translation_alignment')
        output.append({**dict(zip(fields, (a, b, c, d))), 'kind': kind})
        source_end, target_end = b, d
    if source_end != len(original) or target_end != len(translated):
        raise ValueError('invalid_translation_alignment')
    return output


def public_quality(value):
    """Finite public fields: no model path, source snippets, or arbitrary messages."""
    value = value if isinstance(value, dict) else {}
    status = value.get('status')
    if status not in {'checks_passed', 'failed', 'not_checked'}:
        status = 'not_checked'
    issues = value.get('issues')
    issues = sorted({issue for issue in issues if isinstance(issue, str) and issue in ISSUES}) if isinstance(issues, list) else []
    return {'checks_version': CHECKS_VERSION if value.get('checks_version') == CHECKS_VERSION else None,
            'status': status, 'issues': issues, 'human_reviewed': False, 'semantic_verified': False}
