"""Local source validation and closed normalization for AI resume drafts."""
from copy import deepcopy
import json
import re
from uuid import uuid4

from .categories import CATEGORIES, DATE_CATEGORIES, LIST_CATEGORIES, URL_CATEGORIES, RECORD_KINDS, category_kind
from .models import MAX_ENTITIES, MAX_FACTS, partial_date, validate_profile


def _space(value):
    return re.sub(r'\s+', ' ', value).strip()


_MONTHS = {name: i for i, names in enumerate((
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
    ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
    ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'),
    ('dec', 'december')), 1) for name in names}


def normalize_value(key, raw):
    """Return (value, transformation). Never supply missing date precision."""
    if key in LIST_CATEGORIES:
        if type(raw) is not list or not 1 <= len(raw) <= 100 or any(type(v) is not str or not v.strip() for v in raw):
            raise ValueError('invalid_list')
        value = [_space(v) for v in raw]
        if key in URL_CATEGORIES:
            value = [_normalize_url(v) for v in value]
            return value, 'identity' if value == raw else 'url'
        return value, 'identity' if value == raw else 'whitespace'
    if type(raw) is not str or not raw.strip() or len(raw) > 10000:
        raise ValueError('invalid_value')
    value = raw.strip()
    if key in URL_CATEGORIES:
        value = _normalize_url(value)
        return value, 'identity' if value == raw else 'url'
    if key in DATE_CATEGORIES:
        present = key.endswith('.end_date')
        if present and value.casefold() in ('present', 'current'):
            return 'present', 'identity' if value == 'present' else 'date'
        try:
            return partial_date(value, empty=False, present=present), 'identity'
        except ValueError:
            pass
        match = re.fullmatch(r'([A-Za-z]+)\.?\s+(?:(\d{1,2}),?\s+)?(\d{4})', value)
        if match and match[1].casefold() in _MONTHS:
            result = f'{int(match[3]):04d}-{_MONTHS[match[1].casefold()]:02d}'
            if match[2]:
                result += f'-{int(match[2]):02d}'
            return partial_date(result, empty=False), 'date'
        match = re.fullmatch(r'(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})', value)
        if match and match[2].casefold() in _MONTHS:
            result = f'{int(match[3]):04d}-{_MONTHS[match[2].casefold()]:02d}-{int(match[1]):02d}'
            return partial_date(result, empty=False), 'date'
        raise ValueError('ambiguous_date')
    value = _space(value) if '\n' not in value else '\n'.join(line.rstrip() for line in value.splitlines()).strip()
    return value, 'identity' if value == raw else 'whitespace'


def _normalize_url(value):
    # Add only the web scheme to an explicit domain/path; never infer a profile.
    if re.fullmatch(r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?:/[^\s]*)?', value):
        return 'https://' + value
    return value


def _spans(value, blocks, allowed):
    if type(value) is not list or not 1 <= len(value) <= 16:
        raise ValueError('invalid_evidence')
    result, total = [], 0
    for item in value:
        if (type(item) is not dict or set(item) - {'block_id', 'quote', 'start', 'end', 'region'} or
                not {'block_id', 'quote'} <= set(item)):
            raise ValueError('invalid_evidence')
        bid, quote = item['block_id'], item['quote']
        if type(bid) is not str or bid not in allowed or bid not in blocks or type(quote) is not str or not quote.strip():
            raise ValueError('unknown_source')
        text = blocks[bid]['text']
        if 'start' in item or 'end' in item:
            start, end = item.get('start'), item.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != quote:
                raise ValueError('source_offset_mismatch')
        else:
            start = text.find(quote)
            if start < 0:
                # Resolve line wrapping only, then retain the original slice.
                # The second search starts one character later to catch even
                # overlapping matches, which must remain ambiguous.
                pattern = re.compile(r'\s+'.join(re.escape(part) for part in quote.split()))
                match = pattern.search(text)
                if match is None or pattern.search(text, match.start() + 1) is not None:
                    raise ValueError('source_quote_ambiguous')
                start, end = match.span()
                quote = text[start:end]
            elif text.find(quote, start + 1) >= 0:
                raise ValueError('source_quote_ambiguous')
            else:
                end = start + len(quote)
        total += len(quote)
        if total > 16000:
            raise ValueError('evidence_limit')
        span = dict(block_id=bid, start=start, end=end, quote=quote, page=blocks[bid].get('page', 1))
        if 'region' in item:
            span['region'] = deepcopy(item['region'])
        result.append(span)
    return result


def parse_draft(value):
    if isinstance(value, str):
        if len(value) > 256000:
            raise ValueError('draft_limit')
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValueError('duplicate_json_key')
                result[key] = item
            return result
        value = json.loads(value, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_json_number')))
    if type(value) is not dict or set(value) != {'records', 'candidates', 'processed_block_ids'}:
        raise ValueError('invalid_draft_envelope')
    if (type(value['records']) is not list or len(value['records']) > MAX_ENTITIES or
            type(value['candidates']) is not list or len(value['candidates']) > MAX_FACTS or
            type(value['processed_block_ids']) is not list or len(value['processed_block_ids']) > 1000):
        raise ValueError('draft_limit')
    return value


_PRIMARY_LABEL = {'experience': 'experience.employer', 'education': 'education.institution',
    'project': 'project.name', 'certification': 'certification.name', 'award': 'award.name',
    'publication': 'publication.title', 'volunteering': 'volunteering.organization',
    'additional': 'additional.title'}


def _within(spans, scope):
    return all(any(s['block_id'] == anchor['block_id'] and
        anchor['start'] <= s['start'] < s['end'] <= anchor['end'] for anchor in scope) for s in spans)


def _fact(key, raw_value, eid, spans, method, fingerprint):
    values = raw_value if type(raw_value) is list else [raw_value]
    support = _space('\n'.join(s['quote'] for s in spans))
    if not values or any(type(v) is not str or not v.strip() or _space(v) not in support for v in values):
        raise ValueError('unsupported_value')
    value, normalization = normalize_value(key, raw_value)
    return dict(key=key, value=value, entity_id=eid, source='\n'.join(s['quote'] for s in spans)[:2000],
        reviewed=False, provenance=dict(method=method, evidence=spans,
            raw_value=deepcopy(raw_value), normalization=normalization, source_fingerprint=fingerprint))


def _primary_label(kind, rid, candidates, scope, blocks, allowed, method, fingerprint):
    """Recover display metadata only from a unique fully validated identity fact."""
    labels = set()
    for candidate in candidates:
        try:
            if (type(candidate) is not dict or set(candidate) != {'key', 'value', 'entity_id', 'evidence'} or
                    candidate['key'] != _PRIMARY_LABEL[kind] or candidate['entity_id'] != rid):
                continue
            spans = _spans(candidate['evidence'], blocks, allowed)
            if not _within(spans, scope):
                continue
            fact = _fact(candidate['key'], candidate['value'], 'temporary', spans, method, fingerprint)
            label = fact['value']
            if type(label) is not str or not 0 < len(label) <= 200:
                continue
            entity = dict(id='temporary', kind=kind, label=label, start_date='', end_date='')
            validate_profile(dict(id='', revision=0, name='Draft', entities=[entity], facts=[fact]))
            labels.add(label)
        except (ValueError, TypeError, KeyError):
            continue
    return next(iter(labels)) if len(labels) == 1 else None


def validate_draft(value, document, *, allowed_blocks=None, method='text'):
    """Quarantine unsupported candidates without dropping independent facts."""
    if method not in ('text', 'visual'):
        raise ValueError('invalid_evidence_method')
    raw = parse_draft(value)
    blocks = {b['id']: b for b in document['blocks']}
    allowed = set(blocks) if allowed_blocks is None else set(allowed_blocks)
    processed = raw['processed_block_ids']
    if any(type(b) is not str or b not in allowed or b not in blocks for b in processed) or len(set(processed)) != len(processed):
        raise ValueError('unknown_processed_block')
    result = dict(facts=[], entities=[], rejected=[], conflicts=[], warnings=[],
        processed_block_ids=list(processed), source_fingerprint=document['fingerprint'],
        record_evidence={}, complete_for_review=False)
    records, record_spans = {}, {}
    def reject(key, code):
        result['rejected'].append(dict(key=key if type(key) is str and key in CATEGORIES else 'unknown', code=code))
    for item in raw['records']:
        try:
            if type(item) is not dict or set(item) != {'id', 'kind', 'label', 'evidence'}:
                raise ValueError('invalid_record')
            rid, kind, label = item['id'], item['kind'], item['label']
            if (type(rid) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', rid) or rid in records or
                    type(kind) is not str or kind not in RECORD_KINDS or type(label) is not str or not 0 < len(label) <= 200):
                raise ValueError('invalid_record')
            spans = _spans(item['evidence'], blocks, allowed)
            label = _primary_label(kind, rid, raw['candidates'], spans, blocks, allowed,
                method, document['fingerprint']) or label
            if not any(_space(label) in _space(s['quote']) for s in spans):
                raise ValueError('unsupported_record_label')
            entity = dict(id=uuid4().hex, kind=kind, label=label, start_date='', end_date='')
            validate_profile(dict(id='', revision=0, name='Draft', entities=[entity], facts=[]))
            records[rid], record_spans[rid] = entity, spans
            result['entities'].append(entity)
            result['record_evidence'][entity['id']] = spans
        except (ValueError, TypeError, KeyError):
            reject('unknown', 'unsupported_record')
    seen = {}
    for candidate in raw['candidates']:
        key = candidate.get('key') if type(candidate) is dict else None
        try:
            if type(candidate) is not dict or set(candidate) != {'key', 'value', 'entity_id', 'evidence'}:
                raise ValueError('invalid_candidate')
            if type(key) is not str or key not in CATEGORIES:
                raise ValueError('unknown_category')
            rid, kind = candidate['entity_id'], category_kind(key)
            if (kind is None and rid is not None) or (kind is not None and
                    (type(rid) is not str or rid not in records or records[rid]['kind'] != kind)):
                raise ValueError('record_mismatch')
            spans = _spans(candidate['evidence'], blocks, allowed)
            if kind is not None and not _within(spans, record_spans[rid]):
                raise ValueError('record_source_mismatch')
            eid = records[rid]['id'] if kind is not None else None
            fact = _fact(key, candidate['value'], eid, spans, method, document['fingerprint'])
            value = fact['value']
            validate_profile(dict(id='', revision=0, name='Draft', entities=result['entities'], facts=[fact]))
            identity = key, eid
            if identity in seen:
                if seen[identity]['value'] != value:
                    result['conflicts'].append(dict(key=key, entity_id=eid, existing=deepcopy(seen[identity]), proposed=fact))
                continue
            seen[identity] = fact
            result['facts'].append(fact)
            if kind is not None and key in (kind + '.start_date', kind + '.end_date'):
                records[rid][key.rsplit('.', 1)[1]] = value
        except (ValueError, TypeError, KeyError):
            reject(key, 'unsupported_candidate')
    result['entities'] = [e for e in result['entities'] if any(f['entity_id'] == e['id'] for f in result['facts'])]
    result['complete_for_review'] = not result['rejected'] and not result['conflicts']
    return result
