"""Explicit, scoped RAM-only teaching; discard sensitive evidence before retention."""
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from src.desktop_actions.models import now, expired
from src.desktop_context.filters import _CREDENTIAL, redact
from src.reliability.policy import get_safety_policy
from .models import DemonstrationEvent, DemonstrationSession, bounded


class DemonstrationRecorder:
    def __init__(self, *, enabled=lambda: False, policy=None, maximum_events=500):
        self.enabled = enabled
        self.policy = policy or get_safety_policy()
        self.maximum_events = min(500, maximum_events)
        self.session, self.fences = None, ()
        self.lock = RLock()

    def _guard(self):
        if not self.enabled() or not self.policy.allows('desktop_uia'):
            self.cancel()
            raise PermissionError('Teaching is disabled or Safe Mode is active.')

    def start(self, goal, fences):
        with self.lock:
            self._guard()
            if self.session:
                raise ValueError('Finish or cancel the current demonstration first.')
            bounded(goal, 200)
            fences = tuple(fences)
            if not 1 <= len(fences) <= 4 or any(expired(f.expires_at) for f in fences):
                raise ValueError('Choose one to four current application windows.')
            self.fences = fences
            self.session = DemonstrationSession('demo_' + uuid4().hex, goal, tuple(f.allowed_process_name for f in fences))
            from src.observability.performance import emit
            emit('teach_session_started', component='skill')
            return self.session

    @staticmethod
    def safe(element):
        strings = (element.name, element.automation_id, *element.ancestor_path, *element.risk_context,
            *(v for v in element.state.values() if isinstance(v, str)))
        return not (element.protected or _CREDENTIAL.search(' '.join((element.name, element.automation_id, *element.risk_context))) or
            any(redact(v, 65536) != v for v in strings))

    @staticmethod
    def clean(element):
        # Runtime state is needed for inference, but arbitrary property bags are not.
        keys = ('toggle_state', 'selected', 'expanded', 'value', 'range_value', 'scroll_horizontal',
            'scroll_vertical', 'enabled', 'offscreen', 'read_only', 'value_truncated', 'selection_container')
        return replace(element, bounds=None, state={k: element.state[k] for k in keys if k in element.state})

    def _append(self, kind, before=None, after=None, timestamp=None):
        if len(self.session.events) >= self.maximum_events:
            self.session.status = 'paused'
            self.session.incomplete = True
            return
        event = DemonstrationEvent('event_' + uuid4().hex, timestamp or now(), kind, before, after)
        if self.session.events:
            prior = self.session.events[-1]
            # Focus/property/selection duplicate notifications collapse to one state change.
            if (prior.event_type, prior.before, prior.after) == (kind, before, after):
                return
            if kind == 'state_changed' and prior.event_type == kind and prior.after and after and prior.after.fingerprint == after.fingerprint:
                from datetime import datetime
                if (datetime.fromisoformat(event.timestamp) - datetime.fromisoformat(prior.timestamp)).total_seconds() < .25:
                    self.session.events[-1] = replace(event, before=prior.before)
                    return
        self.session.events.append(event)

    def observe(self, before, after, *, invoked_id=None, timestamp=None, unknown_before=False):
        with self.lock:
            self._guard()
            if not self.session or self.session.status != 'recording':
                return
            identity = lambda s: (s.process_id, s.window_id, s.process_name.casefold())
            allowed = {(f.allowed_process_id, f.allowed_window, f.allowed_process_name.casefold()) for f in self.fences if not expired(f.expires_at)}
            if identity(before) != identity(after) or identity(after) not in allowed or after.foreground_window != after.window_id or not after.security_available:
                return
            previous = {e.element_id: e for e in before.elements}
            for element in after.elements:
                old = previous.get(element.element_id)
                if old is None:
                    continue
                if not self.safe(element) or not self.safe(old):
                    if old.state != element.state or invoked_id == element.element_id:
                        self._append('protected_field_interaction_detected')
                    continue
                old, element = self.clean(old), self.clean(element)
                if invoked_id == element.element_id:
                    self._append('invoked', old, element, timestamp)
                elif old.state != element.state or old.name != element.name:
                    self._append('state_changed_unknown_before' if unknown_before else 'state_changed', old, element, timestamp)

    def pause(self):
        with self.lock:
            if self.session and self.session.status == 'recording':
                self.session.status = 'paused'

    def resume(self):
        with self.lock:
            self._guard()
            if self.session and self.session.incomplete:
                raise ValueError('The trace is incomplete. Cancel and teach again.')
            if self.session and self.session.status == 'paused':
                self.session.status = 'recording'

    def finish(self):
        with self.lock:
            self._guard()
            if not self.session:
                raise ValueError('No active demonstration.')
            if self.session.incomplete or any(expired(f.expires_at) for f in self.fences):
                raise ValueError('The trace is incomplete or its teaching scope expired. Cancel and teach again.')
            session = self.session
            session.status, session.ended_at = 'completed', now()
            self.session, self.fences = None, ()
            from src.observability.performance import emit
            emit('teach_session_finished', component='skill')
            return session

    def cancel(self):
        with self.lock:
            if self.session:
                self.session.events.clear()
                self.session.status = 'cancelled'
            self.session, self.fences = None, ()
