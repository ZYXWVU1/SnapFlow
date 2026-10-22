"""Short-lived read authority bound to browser, connection, tab and document."""
from dataclasses import replace
from uuid import uuid4

from .models import BrowserSession, document_identity


class BrowserSessionStore:
    def __init__(self, clock, connection_alive, on_revoke=lambda session_id: None):
        self.clock, self.connection_alive, self.on_revoke = clock, connection_alive, on_revoke
        self._sessions = {}

    def prune(self):
        for session in tuple(self._sessions.values()):
            if session.expires_at <= self.clock() or not self.connection_alive(session.connection_id):
                self.revoke(session.session_id)

    def all(self):
        self.prune()
        return tuple(self._sessions.values())

    def authorize(self, connection, identity, title, access_mode):
        identity = document_identity(identity)
        self.prune()
        for old in tuple(self._sessions.values()):
            if old.connection_id == connection.connection_id and old.tab_id == identity['tab_id']:
                self.revoke(old.session_id)
        if len(self._sessions) >= 32:
            raise PermissionError('Browser assistance session limit reached.')
        session = BrowserSession(uuid4().hex, connection.browser_kind, connection.connection_id,
            connection.extension_origin.removeprefix('chrome-extension://').rstrip('/'),
            identity['tab_id'], 0, identity['document_id'], identity['origin'], title,
            'read', 'authorized', self.clock(), self.clock() + 600, 'active', access_mode)
        self._sessions[session.session_id] = session
        return session

    def require(self, connection_id, session_id, identity=None):
        self.prune()
        session = self._sessions.get(session_id)
        if session is None or session.connection_id != connection_id:
            raise PermissionError('Browser page authorization expired or belongs to another connection.')
        if identity is not None and document_identity(identity) != self.identity(session):
            self.revoke(session_id)
            raise PermissionError('Browser tab, document or origin changed; authorize again.')
        return session

    @staticmethod
    def identity(session):
        return {'tab_id': session.tab_id, 'frame_id': session.frame_id,
                'document_id': session.document_id, 'origin': session.origin}

    def revoke(self, session_id):
        if self._sessions.pop(session_id, None) is not None:
            self.on_revoke(session_id)

    def revoke_owned(self, connection_id, session_id):
        self.prune()
        session = self._sessions.get(session_id)
        if session is not None and session.connection_id != connection_id:
            raise PermissionError('Cannot revoke another browser connection\'s page session.')
        self.revoke(session_id)

    def revoke_connection(self, connection_id):
        for session in tuple(self._sessions.values()):
            if session.connection_id == connection_id:
                self.revoke(session.session_id)

    def clear(self):
        for session_id in tuple(self._sessions):
            self.revoke(session_id)

    def receipt(self, session):
        return {'session_id': session.session_id, 'identity': self.identity(session), 'scope': 'read',
                'access_mode': session.access_mode, 'expires_at': int(session.expires_at * 1000), 'status': 'active'}
