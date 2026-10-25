"""Bounded desktop-to-extension preparation. Approval remains a human sidebar action."""
from dataclasses import dataclass, field
from threading import Event
import time
from uuid import uuid4

from src.agent.models import digest, snapshot, thaw
from .action_policy import validate_spec


@dataclass
class BrowserCommand:
    command_id: str
    session_id: str
    connection_id: str
    identity: dict
    spec: object
    expires_at: float
    authorize: object = field(default=lambda: True, repr=False)
    status: str = 'queued'
    action_id: str | None = None
    result: dict | None = None
    finished: Event = field(default_factory=Event)

    def public(self):
        return dict(command_id=self.command_id, identity=dict(self.identity), spec=thaw(self.spec),
                    expires_at=int(self.expires_at * 1000))


class BrowserCommandStore:
    def __init__(self, manager):
        self.manager = manager
        self.pending = {}

    def enqueue(self, session_id, spec, *, timeout=45, authorize=lambda: True):
        with self.manager.lock:
            self.manager._prune()
            session = next((s for s in self.manager.sessions() if s.session_id == session_id), None)
            if session is None:
                raise PermissionError('Review and authorize the browser page again.')
            self.manager.actions.require(session.connection_id, session_id, self.manager.session_store.identity(session))
            spec = validate_spec(spec, session.origin)
            if len(self.pending) >= 32 or any(c.session_id == session_id for c in self.pending.values()):
                raise PermissionError('Finish or cancel the current browser command first.')
            command = BrowserCommand(uuid4().hex, session_id, session.connection_id,
                self.manager.session_store.identity(session), snapshot(spec),
                min(session.expires_at, self.manager.clock() + min(max(timeout, 1), 60)), authorize)
            self.pending[command.command_id] = command
            self.manager.changed()
            return command

    def poll(self, connection, session_id, identity):
        self.manager.actions.require(connection, session_id, identity)
        for command in tuple(self.pending.values()):
            if command.session_id == session_id and command.connection_id == connection and command.status == 'queued':
                if not self.authorized(command):
                    self.cancel(command)
                    continue
                command.status = 'offered'
                return command.public()
        return None

    def prepare(self, connection, session_id, payload):
        self.manager.actions.require(connection, session_id, payload['identity'])
        command = self.pending.get(payload['command_id'])
        if command is not None and not self.authorized(command):
            self.cancel(command)
            raise PermissionError('Browser command authorization changed.')
        if (command is None or command.status != 'offered' or command.session_id != session_id
                or command.connection_id != connection or command.identity != payload['identity']
                or command.expires_at <= self.manager.clock() or digest(command.spec) != digest(payload['spec'])):
            raise PermissionError('Browser command changed or was already delivered.')
        action = self.manager.actions.prepare(connection, session_id,
            dict(identity=command.identity, spec=payload['spec'], before=payload['before']))
        command.action_id, command.status = action['action_id'], 'awaiting_approval'
        return action

    def authorized(self, command):
        try:
            allowed = command.authorize() is not False
        except Exception:
            allowed = False
        # The guard can re-enter permission pruning. Never use a captured object
        # after its command was removed or expired while that guard ran.
        return allowed and self.pending.get(command.command_id) is command and command.expires_at > self.manager.clock()

    def validate_claim(self, action):
        for command in tuple(self.pending.values()):
            if command.action_id != action['action_id']:
                continue
            if (not self.authorized(command) or command.status != 'awaiting_approval'
                    or self.manager.actions.pending.get(action['action_id'], (None,None))[1] is not action):
                self.cancel(command)
                raise PermissionError('Browser procedure or Agent permission changed before approval.')

    def claimed(self, action_id):
        for command in self.pending.values():
            if command.action_id == action_id:
                command.status = 'executing'

    def complete_action(self, action_id, status):
        for command in tuple(self.pending.values()):
            if command.action_id == action_id:
                self._finish(command, status)

    def _finish(self, command, status):
        if command.finished.is_set():
            return
        command.status = status
        command.result = dict(success=status == 'verified', status=status,
                              browser_verified=status == 'verified', action_id=command.action_id or '')
        self.pending.pop(command.command_id, None)
        command.finished.set()
        self.manager.changed()

    def cancel(self, command, status='cancelled'):
        with self.manager.lock:
            if command.finished.is_set():
                return
            if command.action_id in self.manager.actions.pending:
                _, action = self.manager.actions.pending[command.action_id]
                claimed = action['status'] == 'claimed'
                self.manager.actions.cancel_session(command.session_id, force=True)
                if claimed:
                    status = 'unknown'
            self._finish(command, status)

    def revoke(self, session_id):
        for command in tuple(self.pending.values()):
            if command.session_id != session_id:
                continue
            # The old document always loses authority. Only an already claimed
            # navigation may still deliver its terminal result.
            action = self.manager.actions.pending.get(command.action_id)
            if action and action[1]['status'] == 'claimed' and action[1]['spec']['action_type'] == 'controlled_navigation':
                continue
            self._finish(command, 'unknown' if command.action_id else 'cancelled')

    def prune(self):
        for command in tuple(self.pending.values()):
            if command.expires_at <= self.manager.clock() or command.connection_id not in self.manager._connections:
                self.cancel(command, 'expired')

    def clear(self):
        for command in tuple(self.pending.values()):
            self.cancel(command)


class BrowserExecutionGateway:
    def __init__(self, manager):
        self.manager = manager

    def execute(self, session_id, spec, *, cancelled=lambda: False, guard=lambda: True, timeout=45, terminal_guard=None):
        if cancelled() or guard() is False:
            raise PermissionError('Browser request cancelled before preparation.')
        command = self.manager.commands.enqueue(session_id, spec, timeout=timeout, authorize=lambda:not cancelled() and guard() is not False)
        deadline = time.monotonic() + min(max(timeout, 1), 60)
        try:
            while not command.finished.wait(.05):
                check = (terminal_guard or guard) if command.status == 'executing' and command.spec['action_type'] == 'controlled_navigation' else guard
                if cancelled() or check() is False:
                    self.manager.commands.cancel(command)
                    break
                if time.monotonic() >= deadline:
                    self.manager.commands.cancel(command, 'expired')
                    break
                with self.manager.lock:
                    self.manager._prune()
            return dict(command.result)
        except BaseException:
            self.manager.commands.cancel(command)
            raise
