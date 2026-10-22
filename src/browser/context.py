"""Filtered RAM-only browser snapshots and live-validated ContextSource adapter."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from src.agent.models import canonical, snapshot, thaw
from src.context.models import ContextSource
from .models import document_identity, origin, text

_SENSITIVE = re.compile(r'password|passcode|one.?time|\botp\b|security.?code|credit.?card|card.?number|\bcvv\b|\bcvc\b|secret|token|session.?id|authentication|social.?security', re.I)
_SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|Bearer\s+[A-Za-z0-9_.-]{8,}|(?:password|token|secret|otp|cvv|cvc)\s*[:=]\s*\S+)', re.I)
_SNAPSHOT_FIELDS = {'snapshot_id', 'identity', 'page_title', 'relevant_text', 'headings', 'interactive_elements', 'forms', 'links', 'tables', 'captured_at', 'warnings'}
_ELEMENT_FIELDS = {'element_id', 'role', 'accessible_name', 'label', 'tag_name', 'input_type', 'visible', 'enabled', 'editable', 'selected', 'checked', 'required', 'stable_attributes', 'ancestor_context', 'sensitive'}


def safe_text(value, maximum):
    text(value, maximum, empty=True)
    return _SECRET.sub('[redacted]', value)


def _strings(value, count, length):
    if type(value) is not list or len(value) > count:
        raise ValueError('Browser context list exceeds limit.')
    return [safe_text(item, length) for item in value]


@dataclass(frozen=True)
class BrowserContextSnapshot:
    browser_session_id: str
    data: object

    def __post_init__(self):
        object.__setattr__(self, 'data', snapshot(self.data))

    @property
    def snapshot_id(self):
        return self.data['snapshot_id']


class BrowserDOMContextProvider:
    def __init__(self, sessions, clock, lock):
        self.sessions, self.clock, self.lock = sessions, clock, lock
        self._snapshots = {}

    def drop(self, session_id):
        with self.lock:
            self._snapshots.pop(session_id, None)

    def receive(self, connection_id, session_id, payload):
        with self.lock:
            if type(payload) is not dict or set(payload) != _SNAPSHOT_FIELDS:
                raise ValueError('Invalid browser context fields.')
            canonical(payload, limit=24000)
            session = self.sessions.require(connection_id, session_id, payload['identity'])
            if (type(payload['snapshot_id']) is not str or not re.fullmatch('[a-f0-9]{32}', payload['snapshot_id'])
                    or type(payload['captured_at']) is not int or not -5 <= self.clock() - payload['captured_at'] / 1000 <= 30):
                raise ValueError('Invalid or stale browser snapshot.')
            data = dict(payload)
            data['identity'] = document_identity(payload['identity'])
            data['page_title'] = safe_text(payload['page_title'], 500)
            data['relevant_text'] = safe_text(payload['relevant_text'], 6000)
            data['headings'] = _strings(payload['headings'], 12, 160)
            data['warnings'] = _strings(payload['warnings'], 8, 100)
            elements = payload['interactive_elements']
            if type(elements) is not list or len(elements) > 40:
                raise ValueError('Too many browser elements.')
            kept = []
            for element in elements:
                if type(element) is not dict or set(element) != _ELEMENT_FIELDS:
                    raise ValueError('Invalid browser element schema. Values are unsupported.')
                item = dict(element)
                for key in ('element_id', 'role', 'accessible_name', 'label', 'tag_name', 'input_type'):
                    item[key] = safe_text(element[key], 160)
                for key in ('visible', 'enabled', 'editable', 'selected', 'checked', 'required', 'sensitive'):
                    if type(element[key]) is not bool:
                        raise ValueError('Invalid browser element state.')
                attributes = element['stable_attributes']
                if type(attributes) is not dict or not set(attributes) <= {'id', 'name', 'data-testid', 'role'}:
                    raise ValueError('Unsupported browser target attribute.')
                item['stable_attributes'] = {key: safe_text(value, 100) for key, value in attributes.items()}
                item['ancestor_context'] = _strings(element['ancestor_context'], 3, 100)
                if (not element['visible'] or element['sensitive'] or element['input_type'] in ('password', 'hidden')
                        or _SENSITIVE.search(' '.join(str(v) for v in item.values()))):
                    continue
                kept.append(item)
            data['interactive_elements'] = kept
            ids = {item['element_id'] for item in kept}
            if type(payload['forms']) is not list or len(payload['forms']) > 10:
                raise ValueError('Too many browser forms.')
            data['forms'] = []
            for form in payload['forms']:
                if type(form) is not dict or set(form) != {'form_id', 'element_ids'}:
                    raise ValueError('Invalid browser form schema.')
                data['forms'].append({'form_id': safe_text(form['form_id'], 100),
                    'element_ids': [item for item in _strings(form['element_ids'], 40, 160) if item in ids]})
            if type(payload['links']) is not list or len(payload['links']) > 20:
                raise ValueError('Too many browser links.')
            data['links'] = []
            for link in payload['links']:
                if type(link) is not dict or set(link) != {'label', 'origin'}:
                    raise ValueError('Invalid browser link schema.')
                origin(link['origin'])
                data['links'].append({'label': safe_text(link['label'], 160), 'origin': link['origin']})
            if type(payload['tables']) is not list or len(payload['tables']) > 3:
                raise ValueError('Too many browser tables.')
            data['tables'] = []
            for table in payload['tables']:
                if type(table) is not dict or set(table) != {'headers', 'rows'} or type(table['rows']) is not list or len(table['rows']) > 5:
                    raise ValueError('Invalid browser table schema.')
                data['tables'].append({'headers': _strings(table['headers'], 8, 160),
                    'rows': [_strings(row, 8, 160) for row in table['rows']]})
            result = BrowserContextSnapshot(session_id, data)
            self._snapshots[session_id] = result
            return result

    def snapshots(self):
        with self.lock:
            self.sessions.prune()
            for key, value in tuple(self._snapshots.items()):
                if self.clock() - value.data['captured_at'] / 1000 > 120:
                    self._snapshots.pop(key, None)
            return tuple(self._snapshots.values())

    def latest(self, session_id):
        self.snapshots()
        with self.lock:
            result = self._snapshots.get(session_id)
            if result is None:
                raise ValueError('Browser context is unavailable or expired. Analyze the authorized page again.')
            return result

    def source(self, session_id):
        with self.lock:
            result = self.latest(session_id)
            data = thaw(result.data)
            session = self.sessions.require(next(s.connection_id for s in self.sessions.all() if s.session_id == session_id), session_id)
            content = {'browser_session_id': session_id, 'origin': session.origin, 'document_id': session.document_id,
                'page_title': data['page_title'][:200], 'relevant_text': data['relevant_text'][:1500],
                'headings': data['headings'][:4], 'form_fields': [
                    {'role': item['role'], 'label': (item['label'] or item['accessible_name'])[:80]}
                    for item in data['interactive_elements'][:5]]}
            while len(canonical(content)) > 3000:
                content['relevant_text'] = content['relevant_text'][:max(0, len(content['relevant_text']) - 200)]
                if not content['relevant_text']:
                    content['headings'], content['form_fields'] = [], []
            captured = datetime.fromtimestamp(data['captured_at'] / 1000, timezone.utc).isoformat()
            return ContextSource('B1', 'browser_dom', (data['page_title'] + ' · ' + session.origin)[:500],
                canonical(content), connection_id=session.connection_id,
                resource_uri='browser-session:' + session_id + '/snapshot/' + result.snapshot_id,
                retrieved_at=captured, mime_type='application/json')

    def validate_sources(self, sources):
        with self.lock:
            if len(sources) > 1:
                raise ValueError('Select one authorized browser page at a time.')
            for source in sources:
                match = next((item for item in self.snapshots() if source.resource_uri ==
                    'browser-session:' + item.browser_session_id + '/snapshot/' + item.snapshot_id), None)
                if match is None or source != self.source(match.browser_session_id):
                    raise ValueError('Browser context was replaced, revoked or expired.')
