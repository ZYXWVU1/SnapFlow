"""Desktop authority for bounded, leased connections; never restores grants."""
from dataclasses import dataclass
from threading import RLock
import time
from uuid import uuid4

from src.reliability.policy import get_safety_policy
from .protocol import envelope, extension_origin, validate_envelope


@dataclass(frozen=True)
class BrowserConnection:
    connection_id: str
    extension_origin: str
    browser_kind: str
    extension_version: str
    expires_at: float


class BrowserManager:
    LEASE_SECONDS = 45

    def __init__(self, allowed_origins, *, safety_policy=None, clock=time.time, site_permissions=None, changed=lambda: None):
        self.allowed_origins = frozenset(extension_origin(value) for value in allowed_origins)
        if not self.allowed_origins or len(self.allowed_origins) > 16:
            raise ValueError('Configure explicit approved extension IDs first.')
        self.policy = safety_policy or get_safety_policy()
        self.clock, self.lock = clock, RLock()
        self.enabled = False
        self._connections, self._seen, self._hellos = {}, {}, {}
        from .sessions import BrowserSessionStore
        from .context import BrowserDOMContextProvider
        from .permissions import BrowserSitePermissions
        self.changed = changed
        self.site_permissions = site_permissions or BrowserSitePermissions()
        self.session_store = BrowserSessionStore(self.clock, lambda key:
            self.enabled and self.policy.allows('browser_companion') and
            key in self._connections and self._connections[key].expires_at > self.clock())
        self.context = BrowserDOMContextProvider(self.session_store, self.clock, self.lock)
        self.session_store.on_revoke = self._session_revoked
        from .questions import BrowserQuestionStore
        self.questions = BrowserQuestionStore(self)
        self.question_dispatch = lambda job_id: None

    def set_question_runtime(self, runtime, dispatch):
        self.questions.runtime = runtime
        self.question_dispatch = dispatch

    def _session_revoked(self, session_id):
        self.context.drop(session_id)
        self.questions.revoke_session(session_id)
        self.changed()

    def sessions(self):
        with self.lock:
            self._prune()
            return self.session_store.all()

    def revoke(self, session_id):
        with self.lock:
            self.session_store.revoke(session_id)

    def revoke_site(self, extension_origin, origin):
        with self.lock:
            self.site_permissions.set(extension_origin, origin, False)
            for session in self.session_store.all():
                if (session.origin == origin and session.extension_id ==
                        extension_origin.removeprefix('chrome-extension://').rstrip('/')):
                    self.session_store.revoke(session.session_id)
            self.changed()

    def enable(self):
        with self.lock:
            self.policy.require('browser_companion')
            self.enabled = True

    def disable(self):
        with self.lock:
            self.enabled = False
            self._connections.clear()
            self._seen.clear()
            self._hellos.clear()
            self.session_store.clear()
            self.questions.clear()

    def _prune(self):
        if not self.policy.allows('browser_companion'):
            self.disable()
        for key, value in list(self._connections.items()):
            if value.expires_at <= self.clock():
                self._connections.pop(key)
                self._seen.pop(key, None)
        self._hellos = {key: expiry for key, expiry in self._hellos.items() if expiry > self.clock()}
        self.session_store.prune()

    def connections(self):
        with self.lock:
            self._prune()
            return tuple(self._connections.values())

    def exchange(self, caller_origin, message):
        # The worker dispatch callback runs after releasing browser authority.
        response, dispatch = self._exchange(caller_origin, message)
        if dispatch is not None:
            self.question_dispatch(dispatch)
        return response

    def _exchange(self, caller_origin, message):
        with self.lock:
            self._prune()
            if not self.enabled or caller_origin not in self.allowed_origins:
                raise PermissionError('Browser Companion is stopped or extension is not approved.')
            request = validate_envelope(message, now=self.clock())
            connection_id, request_id = request['connection_id'], request['request_id']
            if request['type'] == 'hello':
                if request_id in self._hellos or len(self._connections) >= 8 or len(self._hellos) >= 1024:
                    raise PermissionError('Duplicate handshake or connection budget exceeded.')
                connection_id = uuid4().hex
                self._hellos[request_id] = self.clock() + 45
                self._seen[connection_id] = {request_id}
                connection = BrowserConnection(connection_id, caller_origin,
                    request['payload']['browser_kind'], request['payload']['extension_version'], self.clock() + 45)
            else:
                connection = self._connections.get(connection_id)
                if connection is None or connection.extension_origin != caller_origin:
                    raise PermissionError('Browser connection expired or identity changed.')
                seen = self._seen[connection_id]
                if request_id in seen:
                    raise PermissionError('Replayed browser message.')
                if len(seen) >= 1024:
                    self._connections.pop(connection_id)
                    self._seen.pop(connection_id)
                    self.session_store.revoke_connection(connection_id)
                    raise PermissionError('Reconnect after connection request budget is exhausted.')
                seen.add(request_id)
                connection = BrowserConnection(connection_id, caller_origin, connection.browser_kind,
                    connection.extension_version, self.clock() + 45)
            self._connections[connection_id] = connection
            kind, payload = request['type'], request['payload']
            session_id = request['session_id']
            dispatch = None
            disconnected = kind == 'disconnect'
            result = {'ok': True, 'kind': 'connection', 'status': 'disconnected' if disconnected else 'connected',
                      'lease_seconds': 0 if disconnected else 45}
            if disconnected:
                self._connections.pop(connection_id)
                self._seen.pop(connection_id, None)
                self.session_store.revoke_connection(connection_id)
            elif kind == 'authorize':
                if payload['access_mode'] == 'site' and not self.site_permissions.allowed(caller_origin, payload['identity']['origin']):
                    raise PermissionError('This site has no explicit SnapFlow site preference.')
                session = self.session_store.authorize(connection, payload['identity'], payload['page_title'], payload['access_mode'])
                session_id = session.session_id
                result = {'ok': True, 'kind': 'session', 'session': self.session_store.receipt(session)}
            elif kind == 'revoke':
                self.session_store.revoke_owned(connection_id, session_id)
                result = {'ok': True, 'kind': 'revoked'}
            elif kind in ('session_status', 'context_snapshot'):
                session = self.session_store.require(connection_id, session_id, payload.get('identity'))
                if kind == 'context_snapshot':
                    context = self.context.receive(connection_id, session_id, payload)
                    self.questions.prune()
                    result = {'ok': True, 'kind': 'snapshot', 'snapshot_id': context.snapshot_id}
                else:
                    result = {'ok': True, 'kind': 'session', 'session': self.session_store.receipt(session)}
            elif kind in ('context_preview', 'question_start', 'question_status', 'question_cancel'):
                self.session_store.require(connection_id, session_id, payload['identity'])
                if kind == 'context_preview':
                    result = {'ok': True, 'kind': 'context_preview',
                              'preview': self.questions.preview(connection_id, session_id, payload['identity'])}
                elif kind == 'question_start':
                    job, dispatch = self.questions.start(connection_id, session_id, payload)
                    result = {'ok': True, 'kind': 'question', 'job': job}
                else:
                    job = self.questions.owned(connection_id, session_id, payload['identity'], payload['job_id'])
                    result = {'ok': True, 'kind': 'question', 'job':
                              self.questions.cancel(job) if kind == 'question_cancel' else job.public()}
            elif kind == 'site_policy':
                self.site_permissions.set(caller_origin, payload['origin'], payload['allowed'])
                if not payload['allowed']:
                    self.revoke_site(caller_origin, payload['origin'])
                result = {'ok': True, 'kind': 'site_policy', **payload}
            if kind not in ('hello', 'health'):
                self.changed()
            return envelope('result', result, connection_id=connection_id, session_id=session_id,
                request_id=request_id, now=self.clock()), dispatch

    def call_cancellable(self, operation, arguments, cancel):
        if cancel.is_set():
            raise PermissionError('Browser request cancelled.')
        if operation != 'exchange' or type(arguments) is not dict or set(arguments) != {'origin', 'message'}:
            raise ValueError('Unsupported browser IPC operation.')
        return self.exchange(arguments['origin'], arguments['message'])
