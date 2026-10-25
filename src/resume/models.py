"""Closed, inert JSON contracts for local personal resume data."""
from copy import deepcopy
from datetime import date
import math
import re
from urllib.parse import urlsplit
from src.resume.categories import (CATEGORIES, DATE_CATEGORIES, LIST_CATEGORIES, RECORD_KINDS,
                                   URL_CATEGORIES, category_kind)


FORMATTERS = frozenset({'identity', 'reviewed_full_name', 'list_join', 'date', 'month', 'option_alias'})
CONTROL_KINDS = frozenset({'text', 'email', 'tel', 'url', 'search', 'textarea', 'select',
                           'select-one', 'date', 'month', 'number', 'textbox', 'combobox',
                           'searchbox', 'spinbutton', 'listbox'})
MAX_PROFILES = 100
MAX_FACTS = 300
MAX_ENTITIES = 50


def _object(value, keys, optional=()):
    if type(value) is not dict or not set(keys) <= set(value) or set(value) - set(keys) - set(optional):
        raise ValueError('Resume record has unknown or missing fields.')


def text(value, limit, *, empty=False):
    if (type(value) is not str or len(value) > limit or (not empty and not value.strip()) or
            any(ord(c) < 32 and c not in '\n\r\t' for c in value) or
            re.search(r'<\s*/?\s*(?:script|iframe|object|embed)\b|javascript\s*:', value, re.I)):
        raise ValueError('Resume text is invalid or exceeds its limit.')
    return value


def identifier(value, *, empty=False):
    if value == '' and empty:
        return value
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid resume record identity.')
    return value


def partial_date(value, *, empty=True, present=False):
    if present and type(value) is str and value.casefold() == 'present':
        return 'present'
    if value == '' and empty:
        return value
    if type(value) is not str or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
        raise ValueError('Use a year, YYYY-MM or YYYY-MM-DD without guessing missing dates.')
    parts = [int(p) for p in value.split('-')]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError as exc:
        raise ValueError('Resume date is invalid.') from exc
    return value


def _url(value):
    url = urlsplit(value)
    if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
        raise ValueError('Resume links must be ordinary HTTP(S) URLs.')


def _provenance(value):
    _object(value, ('method', 'evidence'), ('raw_value', 'normalization', 'source_fingerprint'))
    if 'source_fingerprint' in value and (type(value['source_fingerprint']) is not str or
            not re.fullmatch('[a-f0-9]{64}', value['source_fingerprint'])):
        raise ValueError('Invalid resume source fingerprint.')
    if value['method'] not in ('manual', 'text', 'visual'):
        raise ValueError('Invalid resume provenance method.')
    if 'raw_value' in value:
        raw = value['raw_value']
        if type(raw) is list:
            if not 1 <= len(raw) <= 100:
                raise ValueError('Invalid raw resume value list.')
            for item in raw:
                text(item, 200)
        else:
            text(raw, 10000)
    if 'normalization' in value and value['normalization'] not in ('identity', 'whitespace', 'date', 'url', 'list_join'):
        raise ValueError('Invalid resume normalization.')
    spans = value['evidence']
    if type(spans) is not list or len(spans) > 16:
        raise ValueError('Too many resume evidence spans.')
    total = 0
    for span in spans:
        _object(span, ('block_id', 'start', 'end', 'quote'), ('page', 'region'))
        text(span['block_id'], 128)
        if (type(span['start']) is not int or type(span['end']) is not int or
                not 0 <= span['start'] <= span['end'] <= 10000000):
            raise ValueError('Invalid resume evidence offsets.')
        text(span['quote'], 16000)
        total += len(span['quote'])
        if 'page' in span and (type(span['page']) is not int or not 1 <= span['page'] <= 10000):
            raise ValueError('Invalid resume evidence page.')
        if 'region' in span:
            region = span['region']
            if (type(region) is not list or len(region) != 4 or any(
                    type(n) not in (int, float) or not 0 <= n <= 1 or not math.isfinite(n) for n in region)
                    or region[0] + region[2] > 1 or region[1] + region[3] > 1):
                raise ValueError('Invalid resume evidence region.')
    if total > 16000:
        raise ValueError('Resume evidence is oversized.')


def validate_profile(value):
    _object(value, ('id', 'revision', 'name', 'facts', 'entities'), ('schema_version',))
    value = deepcopy(value)
    if 'schema_version' in value and (type(value['schema_version']) is not int or value['schema_version'] != 2):
        raise ValueError('Unsupported resume profile schema version.')
    identifier(value['id'], empty=True)
    if type(value['revision']) is not int or not 0 <= value['revision'] <= 2147483646:
        raise ValueError('Invalid resume revision.')
    text(value['name'], 200)
    if type(value['facts']) is not list or len(value['facts']) > MAX_FACTS:
        raise ValueError('Too many resume facts.')
    if type(value['entities']) is not list or len(value['entities']) > MAX_ENTITIES:
        raise ValueError('Too many resume records.')
    entities = {}
    for entity in value['entities']:
        _object(entity, ('id', 'kind', 'label', 'start_date', 'end_date'))
        identifier(entity['id'])
        if entity['id'] in entities or entity['kind'] not in RECORD_KINDS:
            raise ValueError('Duplicate or invalid resume record.')
        text(entity['label'], 200)
        partial_date(entity['start_date'])
        entity['end_date'] = partial_date(entity['end_date'], present=True)
        entities[entity['id']] = entity
    seen = set()
    for fact in value['facts']:
        _object(fact, ('key', 'value', 'entity_id', 'source', 'reviewed'), ('provenance',))
        if type(fact['key']) is not str or fact['key'] not in CATEGORIES or type(fact['reviewed']) is not bool:
            raise ValueError('Unknown resume category or review status.')
        key, eid = fact['key'], fact['entity_id']
        if eid is not None:
            identifier(eid)
        kind = category_kind(key)
        if kind is not None:
            if eid not in entities or entities[eid]['kind'] != kind:
                raise ValueError('Resume fact must reference a record of the same kind.')
        elif eid is not None:
            raise ValueError('Personal facts cannot reference repeated records.')
        if (key, eid) in seen:
            raise ValueError('Duplicate resume fact for the same record.')
        seen.add((key, eid))
        if key in LIST_CATEGORIES:
            if type(fact['value']) is not list or not 1 <= len(fact['value']) <= 100:
                raise ValueError('Resume lists must be bounded lists of text.')
            for skill in fact['value']:
                text(skill, 200)
                if key in URL_CATEGORIES:
                    _url(skill)
        else:
            text(fact['value'], 10000)
            if key in DATE_CATEGORIES:
                fact['value'] = partial_date(fact['value'], empty=False, present=key.endswith('.end_date'))
            if key in URL_CATEGORIES:
                _url(fact['value'])
        text(fact['source'], 2000, empty=True)
        if 'provenance' in fact:
            _provenance(fact['provenance'])
    return deepcopy(value)


def validate_rule(value, profile=None):
    _object(value, ('id', 'revision', 'profile_id', 'category', 'entity_selector', 'field_cues',
                    'section_cues', 'control_kind', 'scope', 'formatter', 'enabled', 'confirmed_by_user'))
    identifier(value['id'], empty=True)
    identifier(value['profile_id'])
    if type(value['revision']) is not int or not 0 <= value['revision'] <= 2147483646:
        raise ValueError('Invalid rule revision.')
    if (type(value['category']) is not str or value['category'] not in CATEGORIES or
            type(value['formatter']) is not str or value['formatter'] not in FORMATTERS):
        raise ValueError('Unsupported resume category or formatter.')
    if type(value['control_kind']) is not str or value['control_kind'] not in CONTROL_KINDS:
        raise ValueError('Unsupported resume control kind.')
    if type(value['enabled']) is not bool or value['confirmed_by_user'] is not True:
        raise ValueError('Saving a resume rule requires explicit user confirmation.')
    selector = value['entity_selector']
    if type(selector) is not dict or selector.get('kind') not in (
            'none', 'entity_id', 'group_assignment', 'most_recent_experience', 'most_recent_education'):
        raise ValueError('Invalid resume entity selector.')
    if selector['kind'] == 'entity_id':
        _object(selector, ('kind', 'id'))
        identifier(selector['id'])
    else:
        _object(selector, ('kind',))
    group_kind = category_kind(value['category'])
    repeated = group_kind is not None
    if (repeated and selector['kind'] == 'none') or (not repeated and selector['kind'] != 'none'):
        raise ValueError('Resume selector does not match the category.')
    if selector['kind'].startswith('most_recent_') and selector['kind'] != f'most_recent_{group_kind}':
        raise ValueError('Resume selector has the wrong record kind.')
    scope = value['scope']
    if type(scope) is not dict:
        raise ValueError('Invalid rule scope.')
    if scope.get('kind') == 'reusable':
        _object(scope, ('kind',))
    elif scope.get('kind') == 'site':
        _object(scope, ('kind', 'origin'))
        text(scope['origin'], 2048)
        url = urlsplit(scope['origin'])
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or
                url.path or url.query or url.fragment):
            raise ValueError('Rule scope must contain an HTTP(S) origin only.')
    else:
        raise ValueError('Invalid rule scope.')
    for key in ('field_cues', 'section_cues'):
        if type(value[key]) is not list or len(value[key]) > 12:
            raise ValueError('Too many category cues.')
        for cue in value[key]:
            text(cue, 200)
    if not value['field_cues']:
        raise ValueError('A category rule needs a field cue.')
    if profile is not None:
        if profile['id'] != value['profile_id']:
            raise ValueError('Rule references a different profile.')
        candidates = [f for f in profile['facts'] if f['key'] == value['category'] and f['reviewed']]
        if selector['kind'] == 'entity_id':
            candidates = [f for f in candidates if f['entity_id'] == selector['id']]
        if value['enabled'] and not candidates:
            raise ValueError('Rule has no reviewed profile fact.')
    return deepcopy(value)
