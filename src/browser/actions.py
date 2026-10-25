"""RAM-only previews; a durable single-use claim precedes every web effect."""
from uuid import uuid4

from src.agent.models import digest
from src.reliability.journal import UnavailableJournal
from .action_policy import validate_spec, validate_observation


class BrowserActionStore:
    def __init__(self, manager, journal=None):
        self.manager = manager
        self.journal = journal or UnavailableJournal()
        self.pending = {}
        self.connections = {}

    def require(self, connection, session_id, identity):
        if not self.manager.actions_allowed():
            raise PermissionError('Private Mode blocks page interactions that may transmit content.')
        session = self.manager.session_store.require(connection, session_id, identity)
        if session.authorized_scope != 'interact':
            raise PermissionError('Allow controlled interactions with this page first.')
        return session

    def prepare(self, connection, session_id, payload, *, batch=None):
        session = self.require(connection, session_id, payload['identity'])
        autofill = getattr(self.manager, 'autofill', None)
        if autofill and autofill.active(session_id) and batch is None:
            raise PermissionError('Finish or stop the resume fill before a separate action.')
        trace = getattr(getattr(self.manager, 'teaching', None), 'traces', {}).get(session_id)
        if trace is not None and trace.status == 'recording':
            raise PermissionError('Pause or finish teaching before controlled execution.')
        spec = validate_spec(payload['spec'], session.origin)
        if 'field_ref' in spec['target']:
            if autofill is None:
                raise PermissionError('Resume field references are unavailable.')
            autofill.validate_target(session_id, spec['target'])
        before = validate_observation(payload['before'])
        # One outstanding proposal per document. Replacing it cancels approval.
        self.cancel_session(session_id, previews_only=True)
        if any(owner == session_id and action['status'] == 'claimed' for owner, action in self.pending.values()):
            raise PermissionError('Wait for the current browser action outcome.')
        if len(self.pending) >= 32:
            raise PermissionError('Browser action limit reached.')
        action = dict(action_id=uuid4().hex, identity=dict(payload['identity']), spec=spec, before=before,
            expires_at=int(min(session.expires_at, self.manager.clock() + 30) * 1000), status='preview')
        action['fingerprint'] = digest(action)
        self.pending[action['action_id']] = (session_id, action)
        self.connections[action['action_id']] = connection
        return action

    def owned(self, connection, session_id, payload):
        self.require(connection, session_id, payload['identity'])
        entry = self.pending.get(payload['action_id'])
        if entry is None or entry[0] != session_id:
            raise PermissionError('Browser preview is no longer available.')
        return entry[1]

    def claim(self, connection, session_id, payload):
        action = self.owned(connection, session_id, payload)
        if (action['status'] != 'preview' or action['expires_at'] <= self.manager.clock() * 1000
                or action['fingerprint'] != payload['fingerprint']):
            raise PermissionError('Review a fresh action preview.')
        self.manager.commands.validate_claim(action)
        self.require(connection, session_id, payload['identity'])
        trace = getattr(getattr(self.manager, 'teaching', None), 'traces', {}).get(session_id)
        if trace is not None and trace.status == 'recording':
            raise PermissionError('Pause or finish teaching before controlled execution.')
        if (self.pending.get(action['action_id'], (None,None))[1] is not action or action['status'] != 'preview'
                or action['expires_at'] <= self.manager.clock() * 1000):
            raise PermissionError('Browser preview was revoked during authorization.')
        self.journal.claim_browser(action['action_id'])
        self.journal.approved(action['action_id'])
        self.journal.started(action['action_id'])
        action['status'] = 'claimed'
        self.manager.commands.claimed(action['action_id'])
        return action

    def finish(self, connection, session_id, payload):
        entry = self.pending.get(payload['action_id'])
        # A claimed navigation may report its outcome after the old document is
        # revoked. This terminal receipt grants no access to the destination.
        if (entry and entry[0] == session_id and self.connections.get(payload['action_id']) == connection
                and entry[1]['status'] == 'claimed' and entry[1]['spec']['action_type'] == 'controlled_navigation'
                and entry[1]['identity'] == payload['identity'] and entry[1]['expires_at'] + 10000 > self.manager.clock() * 1000):
            action = entry[1]
        else:
            action = self.owned(connection, session_id, payload)
        if action['status'] != 'claimed':
            raise PermissionError('Browser action was not claimed.')
        status = payload['status']
        self.journal.finish(action['action_id'], {'verified':'completed', 'not_applied':'failed', 'unknown':'unknown'}[status])
        self.pending.pop(action['action_id'])
        self.connections.pop(action['action_id'], None)
        self.manager.commands.complete_action(action['action_id'], status)
        return {**action, 'status':status}

    def cancel(self, connection, session_id, payload):
        action = self.owned(connection, session_id, payload)
        if action['status'] == 'claimed':
            self.journal.finish(action['action_id'], 'unknown')
        self.pending.pop(action['action_id'])
        self.connections.pop(action['action_id'], None)
        self.manager.commands.complete_action(action['action_id'], 'cancelled')
        return {**action, 'status':'cancelled'}

    def cancel_session(self, session_id, *, previews_only=False, force=False):
        for key, (owner, action) in tuple(self.pending.items()):
            if owner != session_id or previews_only and action['status'] != 'preview':
                continue
            if not force and action['status'] == 'claimed' and action['spec']['action_type'] == 'controlled_navigation':
                continue
            if action['status'] == 'claimed':
                try:
                    self.journal.finish(key, 'unknown')
                except Exception:
                    # A failed journal update leaves a durable started record for recovery.
                    pass
            self.pending.pop(key)
            self.connections.pop(key, None)
            self.manager.commands.complete_action(key, 'unknown' if action['status'] == 'claimed' else 'cancelled')

    def prune(self):
        for key, (owner, action) in tuple(self.pending.items()):
            grace = 10000 if action['status'] == 'claimed' else 0
            if action['expires_at'] + grace <= self.manager.clock() * 1000 or self.connections.get(key) not in self.manager._connections:
                if action['status'] == 'claimed':
                    try:
                        self.journal.finish(key, 'unknown')
                    except Exception:
                        pass
                self.pending.pop(key, None)
                self.connections.pop(key, None)
                self.manager.commands.complete_action(key, 'unknown' if action['status'] == 'claimed' else 'expired')

    def clear(self):
        for owner, _ in tuple(self.pending.values()):
            self.cancel_session(owner, force=True)
