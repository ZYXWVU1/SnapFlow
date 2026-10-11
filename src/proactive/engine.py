"""Bounded session/cooldown state. Evaluating never invokes a capability."""
from collections import OrderedDict
import hashlib
import time
from uuid import uuid4

from src.desktop_context.permissions import app_key, BUILTIN_BLOCKED
from .models import AssistanceSession, ProactiveSuggestion
from .rules import candidates


class ProactiveEngine:
    def __init__(self, *, clock=time.monotonic, safe_mode=lambda: False, event_callback=None):
        self.clock, self.safe_mode, self.event_callback = clock, safe_mode, event_callback
        self.session = None
        self.cooldowns = OrderedDict()
        self.pending = {}
        self.last_shown = -1e9

    @property
    def active(self):
        if self.session and (self.safe_mode() or self.session.expires_at is not None and self.clock() >= self.session.expires_at):
            self.stop()
        return self.session is not None

    def start(self, *, duration_minutes=15, enabled_sources=('metadata',), allowed_apps=None,
            suggestion_level='minimal', allow_fullscreen=False):
        if self.safe_mode():
            raise PermissionError('Safe Mode disables Desktop Assistance.')
        if duration_minutes not in (None, 15, 30, 60) or suggestion_level not in ('off', 'minimal', 'normal', 'frequent'):
            raise ValueError('Invalid Assistance Session preferences.')
        if set(enabled_sources) - {'metadata', 'ui', 'selection'}:
            raise ValueError('Clipboard cannot be monitored by Assistance Sessions.')
        self.stop()
        current = self.clock()
        self.session = AssistanceSession(uuid4().hex, current,
            current + duration_minutes * 60 if duration_minutes else None, tuple(enabled_sources),
            tuple(app_key(x) for x in allowed_apps) if allowed_apps else None, suggestion_level, bool(allow_fullscreen))
        return self.session

    def stop(self):
        self.session = None
        self.pending.clear()
        self.cooldowns.clear()
        self.last_shown = -1e9

    def _metric(self, outcome):
        if self.event_callback:
            self.event_callback('desktop_suggestion', outcome=outcome)

    def expire(self):
        for key, item in tuple(self.pending.items()):
            if item.expires_at <= self.clock():
                self.pending.pop(key, None)
                self._metric('expired')

    def evaluate(self, snapshot, *, screenshot_skill=None, screenshot_id=None, related_memory_count=0, safety_window=None):
        if not self.active or self.session.suggestion_level == 'off':
            return ()
        self.expire()
        app = safety_window or snapshot.active_window
        if app is None:
            return ()  # No trustworthy app/fullscreen safety context available.
        if app and (app_key(app.process_name) in BUILTIN_BLOCKED or
                app.fullscreen and not self.session.allow_fullscreen or
                self.session.allowed_apps and app_key(app.process_name) not in self.session.allowed_apps):
            return ()
        if snapshot.focused_element and snapshot.focused_element.protected:
            return ()
        if any('blocked' in warning.casefold() or 'protected' in warning.casefold() for warning in snapshot.warnings):
            return ()
        interval = {'minimal': 300, 'normal': 120, 'frequent': 60}[self.session.suggestion_level]
        if self.clock() - self.last_shown < interval:
            return ()
        offered = candidates(snapshot, screenshot_skill=screenshot_skill,
            selection_allowed='selection' in self.session.enabled_sources, related_memory_count=related_memory_count)
        for kind, title, description, goal, trigger in offered:
            content = screenshot_id if trigger == 'explicit_screenshot' else snapshot.selection.text if snapshot.selection else kind
            digest = hashlib.sha256((kind + ':' + str(content)).encode('utf-8')).hexdigest()
            if self.cooldowns.get(digest, 0) > self.clock():
                self._metric('duplicate_suppressed')
                continue
            suggestion = ProactiveSuggestion(uuid4().hex, kind, title, description, trigger,
                (snapshot.snapshot_id,), goal, 'agent:plan_review', 'read_only', self.clock() + 120, digest)
            self.cooldowns[digest] = self.clock() + 1800
            while len(self.cooldowns) > 256:
                self.cooldowns.popitem(last=False)
            self.pending[suggestion.suggestion_id] = suggestion
            self.last_shown = self.clock()
            self._metric('shown')
            return (suggestion,)
        return ()

    def dismiss(self, suggestion_id):
        if self.pending.pop(suggestion_id, None):
            self._metric('dismissed')

    def accept(self, suggestion_id):
        if not self.active:
            raise ValueError('Assistance Session ended.')
        self.expire()
        item = self.pending.pop(suggestion_id, None)
        if item is None:
            raise ValueError('Suggestion expired or was removed.')
        self._metric('accepted')
        return item.goal
