"""Closed v3 read-only protocol. No page interaction or arbitrary scripting."""
import re
import time
from uuid import uuid4

from src.agent.models import canonical, snapshot, thaw

PROTOCOL = 'snapflow-browser'
VERSION = 3
MAX_MESSAGE_BYTES = 32768
HOST_NAME = 'com.snapflow.browser'
_FIELDS = {'protocol', 'version', 'request_id', 'connection_id', 'session_id', 'type', 'payload', 'expires_at'}
_ID = re.compile(r'[a-f0-9]{32}')
_EXTENSION = re.compile(r'chrome-extension://[a-p]{32}/')


def extension_origin(value):
    if type(value) is not str or not _EXTENSION.fullmatch(value):
        raise ValueError('Invalid extension origin.')
    return value


def envelope(kind, payload, *, connection_id=None, session_id=None, request_id=None, now=None):
    current = time.time() if now is None else now
    return {'protocol': PROTOCOL, 'version': VERSION, 'request_id': request_id or uuid4().hex,
            'connection_id': connection_id, 'session_id': session_id, 'type': kind,
            'payload': thaw(snapshot(payload)), 'expires_at': int(current * 1000) + 15000}


def validate_envelope(value, *, now=None, response=False):
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError('Invalid browser message fields.')
    canonical(value, limit=MAX_MESSAGE_BYTES)
    if value['protocol'] != PROTOCOL or type(value['version']) is not int or value['version'] != VERSION:
        raise ValueError('SnapFlow Browser Companion needs an update.')
    if type(value['request_id']) is not str or not _ID.fullmatch(value['request_id']):
        raise ValueError('Invalid browser request identity.')
    connection = value['connection_id']
    if connection is not None and (type(connection) is not str or not _ID.fullmatch(connection)):
        raise ValueError('Invalid browser connection identity.')
    session_id = value['session_id']
    if session_id is not None and (type(session_id) is not str or not _ID.fullmatch(session_id)):
        raise ValueError('Invalid browser page session identity.')
    current = int((time.time() if now is None else now) * 1000)
    if type(value['expires_at']) is not int or not current < value['expires_at'] <= current + 30000:
        raise ValueError('Browser message is expired or has an invalid lifetime.')
    kind, payload = value['type'], value['payload']
    if type(payload) is not dict:
        raise ValueError('Invalid browser payload.')
    if response:
        if kind != 'result' or type(payload.get('ok')) is not bool:
            raise ValueError('Invalid browser response.')
        if payload['ok']:
            kind = payload.get('kind')
            valid = connection is not None
            if kind == 'connection':
                valid = valid and set(payload) == {'ok', 'kind', 'status', 'lease_seconds'} and session_id is None
                valid = valid and ((payload['status'], payload['lease_seconds']) in (('connected', 45), ('disconnected', 0)))
                valid = valid and type(payload['lease_seconds']) is int
            elif kind == 'session':
                valid = valid and set(payload) == {'ok', 'kind', 'session'} and session_id is not None
                _session_receipt(payload.get('session'), session_id)
            elif kind == 'revoked':
                valid = valid and set(payload) == {'ok', 'kind'} and session_id is not None
            elif kind == 'snapshot':
                valid = valid and set(payload) == {'ok', 'kind', 'snapshot_id'} and session_id is not None
                valid = valid and type(payload['snapshot_id']) is str and bool(_ID.fullmatch(payload['snapshot_id']))
            elif kind == 'context_preview':
                valid = valid and session_id is not None and set(payload) == {'ok', 'kind', 'preview'}
                _preview_receipt(payload.get('preview'), session_id)
            elif kind == 'question':
                valid = valid and session_id is not None and set(payload) == {'ok', 'kind', 'job'}
                _question_receipt(payload.get('job'))
                canonical(payload, limit=16384)
            elif kind == 'site_policy':
                valid = valid and set(payload) == {'ok', 'kind', 'origin', 'allowed'} and session_id is None
                from .models import origin
                origin(payload['origin'])
                valid = valid and type(payload['allowed']) is bool
            else:
                valid = False
            if not valid:
                raise ValueError('Invalid browser success response.')
        elif set(payload) != {'ok', 'code'} or payload['code'] not in ('permission', 'protocol', 'unavailable'):
            raise ValueError('Invalid browser failure response.')
    elif kind == 'hello':
        if (connection is not None or session_id is not None or set(payload) != {'browser_kind', 'extension_version'}
                or payload['browser_kind'] not in ('chrome', 'edge')
                or type(payload['extension_version']) is not str
                or not re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}', payload['extension_version'])):
            raise ValueError('Invalid browser handshake.')
    elif kind in ('health', 'disconnect'):
        if payload or connection is None or session_id is not None:
            raise ValueError('Invalid browser lifecycle request.')
    elif kind == 'authorize':
        from .models import document_identity, text
        if connection is None or session_id is not None or set(payload) != {'identity', 'page_title', 'scope', 'access_mode'}:
            raise ValueError('Invalid browser authorization request.')
        document_identity(payload['identity'])
        text(payload['page_title'], 500, empty=True)
        if payload['scope'] != 'read' or payload['access_mode'] not in ('active_tab', 'site'):
            raise ValueError('Only explicit read-only browser authorization is supported.')
    elif kind in ('context_preview', 'question_start', 'question_status', 'question_cancel'):
        from .models import document_identity, text
        fields = {'identity'}
        if kind == 'question_start':
            fields |= {'snapshot_id', 'source_digest', 'routing_revision', 'question', 'client_question_id', 'runtime_choice'}
        elif kind in ('question_status', 'question_cancel'):
            fields.add('job_id')
        if connection is None or session_id is None or set(payload) != fields:
            raise ValueError('Invalid browser question request.')
        document_identity(payload['identity'])
        if kind == 'question_start':
            for key in ('snapshot_id', 'client_question_id'):
                if type(payload[key]) is not str or not _ID.fullmatch(payload[key]):
                    raise ValueError('Invalid browser question identity.')
            for key in ('source_digest', 'routing_revision'):
                if type(payload[key]) is not str or not re.fullmatch('[a-f0-9]{64}', payload[key]):
                    raise ValueError('Invalid browser source receipt.')
            text(payload['question'], 500)
            if not payload['question'].strip() or payload['runtime_choice'] not in (None, 'local_only', 'cloud_only'):
                raise ValueError('Invalid browser question or routing choice.')
        elif kind != 'context_preview':
            if type(payload['job_id']) is not str or not _ID.fullmatch(payload['job_id']):
                raise ValueError('Invalid browser job identity.')
    elif kind == 'session_status':
        from .models import document_identity
        if connection is None or session_id is None or set(payload) != {'identity'}:
            raise ValueError('Invalid browser session status request.')
        document_identity(payload['identity'])
    elif kind == 'revoke':
        if connection is None or session_id is None or payload:
            raise ValueError('Invalid browser session revocation.')
    elif kind == 'context_snapshot':
        from .models import document_identity
        if connection is None or session_id is None or set(payload) != {
                'snapshot_id', 'identity', 'page_title', 'relevant_text', 'headings', 'interactive_elements',
                'forms', 'links', 'tables', 'captured_at', 'warnings'}:
            raise ValueError('Invalid browser context message.')
        document_identity(payload['identity'])
    elif kind == 'site_policy':
        from .models import origin
        if connection is None or session_id is not None or set(payload) != {'origin', 'allowed'} or type(payload['allowed']) is not bool:
            raise ValueError('Invalid browser site policy request.')
        origin(payload['origin'])
    else:
        raise ValueError('Unsupported browser operation.')
    return thaw(snapshot(value))


def _session_receipt(value, session_id):
    from .models import document_identity
    if type(value) is not dict or set(value) != {'session_id', 'identity', 'scope', 'access_mode', 'expires_at', 'status'}:
        raise ValueError('Invalid browser session receipt.')
    document_identity(value['identity'])
    if (value['session_id'] != session_id or value['scope'] != 'read' or value['status'] != 'active'
            or value['access_mode'] not in ('active_tab', 'site') or type(value['expires_at']) is not int):
        raise ValueError('Invalid browser session receipt.')


def _question_receipt(value):
    from .questions import JOB_CODES, JOB_STATES
    from .models import text
    if type(value) is not dict or set(value) != {'job_id', 'state', 'code', 'answer', 'source_ids', 'execution'}:
        raise ValueError('Invalid browser question receipt.')
    if (type(value['job_id']) is not str or not _ID.fullmatch(value['job_id'])
            or value['state'] not in JOB_STATES or value['code'] not in JOB_CODES):
        raise ValueError('Invalid browser question state.')
    text(value['answer'], 16384, empty=True)
    if type(value['source_ids']) is not list or value['source_ids'] not in ([], ['B1']):
        raise ValueError('Invalid browser answer attribution.')
    execution = value['execution']
    if (type(execution) is not dict or set(execution) != {'route', 'model'}
            or execution['route'] not in ('local', 'cloud', 'unknown')):
        raise ValueError('Invalid browser execution receipt.')
    text(execution['model'], 200, empty=True)
    if value['state'] != 'completed' and (value['answer'] or value['source_ids']):
        raise ValueError('Nonterminal browser answers are unavailable.')


def _preview_receipt(value, session_id):
    from .models import document_identity, origin, text
    fields = {'session_id', 'identity', 'snapshot_id', 'source_digest', 'routing_revision',
              'captured_at', 'expires_at', 'title', 'origin', 'content', 'source_id', 'routing', 'readiness'}
    if type(value) is not dict or set(value) != fields or value['session_id'] != session_id:
        raise ValueError('Invalid browser preview receipt.')
    document_identity(value['identity'])
    origin(value['origin'])
    for key, length in (('snapshot_id', 32), ('source_digest', 64), ('routing_revision', 64)):
        if type(value[key]) is not str or not re.fullmatch('[a-f0-9]{%d}' % length, value[key]):
            raise ValueError('Invalid browser preview identity.')
    if (type(value['captured_at']) is not int or type(value['expires_at']) is not int
            or value['expires_at'] <= value['captured_at'] or value['source_id'] != 'B1'
            or value['readiness'] not in ('ready', 'no_readable_context')):
        raise ValueError('Invalid browser preview lifetime.')
    text(value['title'], 200, empty=True)
    text(value['content'], 3000, empty=True)
    routing = value['routing']
    if (type(routing) is not dict or set(routing) != {'mode', 'private_mode', 'allow_cloud_fallback'}
            or routing['mode'] not in ('automatic', 'prefer_local', 'local_only', 'cloud_only', 'ask_every_time')
            or type(routing['private_mode']) is not bool or type(routing['allow_cloud_fallback']) is not bool):
        raise ValueError('Invalid browser preview policy.')
