"""Explicit scoped teaching; sanitized traces stay in RAM until Finish saves a draft."""
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

from src.agent.models import canonical, snapshot, thaw
from src.automation.compiler import DemonstrationInterpreter
from src.automation.models import bounded
from src.automation.storage import AutomationSkillStore
from src.browser.models import document_identity


@dataclass
class BrowserTeachingTrace:
    trace_id: str
    connection_id: str
    session_id: str
    identity: dict
    goal_description: str
    origin: str
    expires_at: int
    library: object = field(repr=False)
    events: tuple = ()
    sequence: int = 0
    status: str = 'recording'
    incomplete: bool = False


class BrowserTeachingStore:
    def __init__(self, manager, *, store_provider=lambda: None, enabled=lambda: False):
        self.manager, self.store_provider, self.enabled = manager, store_provider, enabled
        self.traces = {}

    def _require(self, connection, session_id, identity):
        try:
            if not self.enabled():
                self.clear()
                raise PermissionError('Browser teaching is disabled.')
            return self.manager.actions.require(connection, session_id, identity)
        except (PermissionError, ValueError):
            self.revoke(session_id)
            raise

    def _library(self):
        library = self.store_provider()
        if not isinstance(library, AutomationSkillStore):
            raise PermissionError('The existing Automation Skills library is unavailable.')
        return library

    @staticmethod
    def receipt(trace, **extra):
        return dict(trace_id=trace.trace_id, status=trace.status, event_count=len(trace.events),
            incomplete=trace.incomplete, sequence=trace.sequence, expires_at=trace.expires_at, **extra)

    def _trace(self, connection, session_id, payload, *, append=False):
        fields = {'identity', 'trace_id'} | ({'sequence', 'events', 'incomplete'} if append else set())
        if type(payload) is not dict or set(payload) != fields:
            raise ValueError('Invalid browser teaching request fields.')
        self._require(connection, session_id, payload['identity'])
        trace = self.traces.get(session_id)
        if (trace is None or trace.connection_id != connection or trace.trace_id != payload['trace_id']
                or trace.identity != document_identity(payload['identity'])):
            raise PermissionError('Teaching scope changed. Start a new explicit teaching session.')
        return trace

    def start(self, connection, session_id, identity, title):
        with self.manager.lock:
            if getattr(self.manager, 'autofill', None) and self.manager.autofill.active(session_id):
                raise PermissionError('Finish or stop the resume fill before recording a task.')
            session = self._require(connection, session_id, identity)
            library = self._library()
            bounded(title, 200)
            # Validate goal privacy before retaining it, even when the trace is empty.
            DemonstrationInterpreter().compile_browser(SimpleNamespace(status='completed', incomplete=False,
                goal_description=title, origin=session.origin, events=(dict(action_type='manual_checkpoint',
                    target=dict(role='manual', name='Manual step'), value=None),)))
            if session_id in self.traces:
                raise PermissionError('Finish or discard the existing browser teaching session first.')
            if len(self.traces) >= 8:
                raise PermissionError('Browser teaching session limit reached.')
            self.manager.actions.prune()
            if any(owner == session_id and action['status'] in ('preview', 'claimed')
                   for owner, action in self.manager.actions.pending.values()):
                raise PermissionError('Finish or cancel the controlled action before teaching.')
            trace = BrowserTeachingTrace(uuid4().hex, connection, session_id, document_identity(identity),
                title, session.origin, int(session.expires_at * 1000), library)
            self.traces[session_id] = trace
            return self.receipt(trace)

    def append(self, connection, session_id, payload):
        with self.manager.lock:
            trace = self._trace(connection, session_id, payload, append=True)
            if trace.status != 'recording':
                raise PermissionError('Teaching is paused. Finish or discard it; capture cannot resume automatically.')
            sequence, events, incomplete = payload['sequence'], payload['events'], payload['incomplete']
            if type(sequence) is not int or sequence != trace.sequence + 1 or sequence > 2**31 - 1:
                raise ValueError('Duplicate, stale or out-of-order teaching batch.')
            if (type(incomplete) is not bool or type(events) is not list or len(events) > 20
                    or not events and not incomplete):
                raise ValueError('A teaching batch needs bounded events or an explicit capture-loss signal.')
            canonical(events, limit=24000)
            # Validate the complete batch before copying any event into the trace.
            if events:
                DemonstrationInterpreter().compile_browser(SimpleNamespace(status='completed', incomplete=False,
                    goal_description=trace.goal_description, origin=trace.origin, events=tuple(events)))
            trace.sequence = sequence
            if incomplete or len(trace.events) + len(events) > 20:
                trace.incomplete, trace.status = True, 'paused'
                return self.receipt(trace)
            trace.events = (*trace.events, *(thaw(snapshot(event)) for event in events))
            return self.receipt(trace)

    def pause(self, connection, session_id, payload):
        with self.manager.lock:
            trace = self._trace(connection, session_id, payload)
            trace.status = 'paused'
            return self.receipt(trace)

    def finish(self, connection, session_id, payload):
        with self.manager.lock:
            trace = self._trace(connection, session_id, payload)
            if trace.incomplete:
                raise ValueError('The teaching trace is incomplete. Discard it and teach again.')
            if self._library() is not trace.library:
                self.revoke(session_id)
                raise PermissionError('The Automation Skills library changed. Start a new teaching session.')
            trace.status = 'completed'
            try:
                skill = DemonstrationInterpreter().compile_browser(trace)
                trace.library.save_draft(skill)
                result = self.receipt(trace, version_id=skill.version_id)
            except Exception:
                trace.status, trace.incomplete = 'paused', True
                raise
            self.revoke(session_id)
            return result

    def discard(self, connection, session_id, payload):
        with self.manager.lock:
            trace = self._trace(connection, session_id, payload)
            trace.events, trace.goal_description, trace.status = (), '', 'discarded'
            result = self.receipt(trace)
            self.revoke(session_id)
            return result

    def revoke(self, session_id):
        with self.manager.lock:
            trace = self.traces.pop(session_id, None)
            if trace is not None:
                trace.events, trace.goal_description = (), ''
                trace.status = 'discarded'

    def clear(self):
        with self.manager.lock:
            for session_id in tuple(self.traces):
                self.revoke(session_id)
