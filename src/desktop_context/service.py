"""Capture only authorized reads; publish immutable temporary snapshots."""
from dataclasses import replace
from threading import RLock
import time

from .filters import filter_elements, redact, sensitive
from .models import DesktopContextSnapshot, SelectionContext
from .snapshot import DesktopContextRelevanceFilter, is_stale


class DesktopContextService:
    def __init__(self, permissions, *, window_provider=None, uia_provider=None, clipboard_provider=None,
            safe_mode=None, event_callback=None):
        from .active_window import ActiveWindowProvider
        from .uia import UIAutomationContextProvider
        from .clipboard import read_clipboard_text
        self.permissions = permissions
        self.window_provider = window_provider or ActiveWindowProvider(
            title_allowed=lambda app: permissions.allows('metadata', app))
        self.uia_provider = uia_provider or UIAutomationContextProvider().capture
        self.clipboard_provider = clipboard_provider or read_clipboard_text
        from src.reliability.policy import get_safety_policy
        self.safe_mode = safe_mode or (lambda: get_safety_policy().safe_mode)
        self.event_callback = event_callback
        self.lock = RLock()
        self.latest = None
        self.target = None
        self.safety_window = None
        self.excluded = frozenset()
        self.epoch = 0
        self.timings = {}

    def clear(self, *, revoke=False):
        with self.lock:
            self.latest = None
            self.target = None
            self.safety_window = None
            self.excluded = frozenset()
            self.epoch += 1
            if revoke:
                self.permissions.revoke()

    def capture(self, *, once=(), screenshot_reference=None, cancelled=lambda: False, requested_sources=None):
        if set(once) - {'ui', 'selection', 'clipboard'}:
            raise ValueError('Unknown desktop source.')
        version, epoch = self.permissions.version, self.epoch
        def invalidated():
            return cancelled() or version != self.permissions.version or epoch != self.epoch
        def boundary():
            if invalidated():
                raise ValueError('Desktop permission changed during capture.')
        def target_boundary():
            boundary()
            import os
            current = self.window_provider()
            if not window or not current or (current.process_id != os.getpid() and
                    (window.hwnd, window.process_id) != (current.hwnd, current.process_id)):
                raise ValueError('Desktop target changed before sensitive read.')
            if self.permissions.is_blocked(app) or (current.process_id != os.getpid() and
                    self.permissions.is_blocked(current.process_name)):
                raise ValueError('Desktop target is blocked.')
            boundary()
        started = time.monotonic()
        warnings = []
        window = self.window_provider()
        self.timings['active_window_ms'] = (time.monotonic() - started) * 1000
        app = window.process_name if window else None
        allowed = {s: self.permissions.allows(s, app, once) for s in ('metadata', 'ui', 'selection', 'clipboard')}
        if requested_sources is not None:
            if set(requested_sources) - {'metadata', 'ui', 'selection', 'clipboard'}:
                raise ValueError('Unknown requested desktop sources.')
            allowed = {s: permitted and s in requested_sources for s, permitted in allowed.items()}
        if self.safe_mode():
            allowed['ui'] = allowed['selection'] = False
            warnings.append('Safe Mode: UI Automation and Assistance Sessions are disabled.')
        if self.permissions.is_blocked(app):
            warnings.append('Limited context available. Application context is blocked or unidentified.')
        if not window or not window.hwnd:
            warnings.append('Limited context available. Active window unavailable.')
        elements, focused, selection, clipboard = (), None, None, None
        if window and window.hwnd and (allowed['ui'] or allowed['selection']):
            boundary()
            uia_started = time.monotonic()
            result = self.uia_provider(window, include_ui=allowed['ui'], include_selection=allowed['selection'],
                cancelled=lambda: invalidated() or self.safe_mode())
            boundary()
            self.timings['uia_and_selection_ms'] = (time.monotonic() - uia_started) * 1000
            warnings.extend(result.warnings)
            safe = filter_elements(result.elements)
            raw_focus = next((e for e in result.elements if e.element_id == result.focused_id), None)
            if raw_focus and sensitive(raw_focus):
                warnings.append('Protected focused control. Suggestions are suppressed.')
            if allowed['ui']:
                elements = safe
                focused = next((e for e in safe if e.element_id == result.focused_id), None)
            # A selection must be explicitly attributed to a nonprotected focused control.
            if allowed['selection'] and raw_focus and not sensitive(raw_focus) and result.selected_text:
                selection = SelectionContext(redact(result.selected_text, 3000), app, None)
        if allowed['clipboard']:
            target_boundary()
            clipboard_started = time.monotonic()
            try:
                clipboard = redact(self.clipboard_provider(), 3000) or None
            except Exception:
                warnings.append('Current clipboard unavailable.')
            self.timings['clipboard_ms'] = (time.monotonic() - clipboard_started) * 1000
        current = self.window_provider()
        import os
        if (cancelled() or version != self.permissions.version or epoch != self.epoch or
                (window and (not current or current.process_id != os.getpid() and
                    (window.hwnd, window.process_id) != (current.hwnd, current.process_id)))):
            raise ValueError('Desktop context changed during capture. Capture again.')
        if window:
            window = replace(window, window_title=redact(window.window_title, 500) if allowed['metadata'] else None)
        snapshot = DesktopContextSnapshot(active_window=window if allowed['metadata'] else None,
            focused_element=focused, ui_elements=elements, selection=selection, clipboard_text=clipboard,
            screenshot_reference=screenshot_reference, permissions=tuple(allowed.items()),
            permission_version=version, warnings=tuple(dict.fromkeys(warnings)))
        with self.lock:
            if cancelled() or version != self.permissions.version or epoch != self.epoch:
                raise ValueError('Desktop permission revoked during capture.')
            self.latest, self.excluded = snapshot, frozenset()
            self.target = (window.hwnd, window.process_id) if window and window.hwnd else None
            self.safety_window = replace(window, window_title=None) if window else None
        self.timings['capture_ms'] = (time.monotonic() - started) * 1000
        if self.event_callback:
            self.event_callback('desktop_capture', success=True, source_count=len(self.sources(snapshot.snapshot_id, check_window=False)))
        return snapshot

    def validate(self, snapshot_id, *, check_window=True):
        with self.lock:
            snapshot = self.latest
            if (snapshot is None or snapshot.snapshot_id != snapshot_id or is_stale(snapshot) or
                    snapshot.permission_version != self.permissions.version):
                raise ValueError('Desktop context is stale or revoked. Capture and review it again.')
        if check_window and self.target:
            current = self.window_provider()
            # SnapFlow may be foreground during preview/request; retain the explicit target,
            # but a different external foreground app invalidates it.
            import os
            if not current or not current.hwnd or current.process_id != os.getpid() and (
                    current.hwnd, current.process_id) != self.target:
                raise ValueError('The active application changed. Capture and review context again.')
            if (current and snapshot.active_window and current.process_id != os.getpid() and
                    snapshot.active_window.window_title and redact(current.window_title, 500) != snapshot.active_window.window_title):
                raise ValueError('The active window title changed. Capture and review context again.')
        return snapshot

    def sources(self, snapshot_id, goal='', *, check_window=True):
        snapshot = self.validate(snapshot_id, check_window=check_window)
        return DesktopContextRelevanceFilter().sources(snapshot, goal, self.excluded)

    def validate_sources(self, sources):
        if not sources:
            return
        snapshot_id = sources[0].resource_uri
        live = {s.source_type: s for s in self.sources(snapshot_id)}
        if any(s.resource_uri != snapshot_id or s.source_type not in live or
                live[s.source_type].content != s.content for s in sources):
            raise ValueError('Selected desktop context changed or was removed.')

    def remove_source(self, source_type):
        from .snapshot import DESKTOP_TYPES
        if source_type not in DESKTOP_TYPES:
            raise ValueError('Unknown desktop source.')
        with self.lock:
            self.excluded = self.excluded | {source_type}
            if self.latest:
                changes = {'desktop_window': {'active_window': None},
                    'desktop_ui': {'ui_elements': (), 'focused_element': None},
                    'selected_text': {'selection': None}, 'clipboard': {'clipboard_text': None}}
                self.latest = replace(self.latest, **changes[source_type])
