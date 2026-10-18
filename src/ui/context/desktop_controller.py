"""Own one desktop worker and explicit, visible Assistance Session lifecycle."""
from dataclasses import replace
import os
import time
from threading import Event
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout, QWidget
from src.config import save_config
from src.desktop_context.active_window import ActiveWindowProvider
from src.desktop_context.change_detector import ContextChangeDetector
from src.desktop_context.permissions import DesktopContextPermissions
from src.desktop_context.service import DesktopContextService
from src.observability.performance import emit
from src.proactive.engine import ProactiveEngine
from src.ui.design.components import AppButton
from src.ui.proactive.suggestion_banner import SuggestionBanner
from .context_permission_dialog import ContextPermissionDialog, DesktopPolicyWidget
from .desktop_context_panel import DesktopContextPanel


class _Signals(QObject):
    finished = Signal(object, object, str)


class _CaptureJob(QRunnable):
    def __init__(self, service, once, screenshot_reference, requested_sources):
        super().__init__()
        self.service, self.once, self.screenshot_reference = service, once, screenshot_reference
        self.requested_sources = requested_sources
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        snapshot, error = None, ''
        try:
            snapshot = self.service.capture(once=self.once, screenshot_reference=self.screenshot_reference,
                cancelled=self.cancelled.is_set, requested_sources=self.requested_sources)
        except Exception:
            error = 'Context unavailable or changed during capture. Switch to the target app and capture again.'
        self.signals.finished.emit(self, snapshot, error)


class DesktopController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        from src.reliability.policy import get_safety_policy
        safe_mode = lambda: owner.safe_mode or get_safety_policy().safe_mode
        self.permissions = DesktopContextPermissions()
        self.service = DesktopContextService(self.permissions, safe_mode=safe_mode,
            event_callback=self._metric)
        self.engine = ProactiveEngine(safe_mode=safe_mode, event_callback=self._metric)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.worker = None
        self.panel = DesktopContextPanel(owner.main_window)
        self.panel.capture_requested.connect(self.request_capture)
        self.panel.clear_requested.connect(self.clear)
        self.panel.source_removed.connect(self.remove)
        self.panel.assistant_requested.connect(self.open_assistant)
        self.panel.agent_requested.connect(self.open_agent)
        self.panel.settings_requested.connect(self.open_settings)
        self.panel.start_requested.connect(self.request_start)
        self.panel.stop_requested.connect(self.stop)
        self.indicator = owner.main_window.assistance_indicator
        self.indicator.clicked.connect(self.show)
        self.banner = SuggestionBanner(owner.main_window)
        owner.main_window.content_layout.insertWidget(1, self.banner)
        self.result_banner = SuggestionBanner(owner.result)
        owner.result.layout().insertWidget(1, self.result_banner)
        for banner in (self.banner, self.result_banner):
            banner.accepted.connect(self.accept)
            banner.dismissed.connect(self.dismiss)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self._delayed_capture)
        self.delayed_sources = ()
        self.foreground = ActiveWindowProvider(title_allowed=lambda app: False)
        self.detector = ContextChangeDetector()
        from src.desktop_context.events import FocusEventObserver
        self.focus_events = FocusEventObserver()
        self.focus_pending_since = None
        self.last_refresh = 0
        self.settings_dialog = None
        self.session_action = None
        self.apply_preferences()
        # Must never restore a persisted session/grant after startup or crash.
        self._status()

    @property
    def busy(self):
        return self.worker is not None

    def _metric(self, event, **properties):
        emit(event, component='context', properties=properties)

    def apply_preferences(self):
        self.stop()
        config = self.owner.config
        self.permissions.configure(metadata=config.desktop_metadata_enabled,
            ui=config.desktop_ui_policy, selection=config.desktop_selection_policy,
            clipboard=config.desktop_clipboard_policy, blocked=config.desktop_blocked_apps,
            apps=config.desktop_app_policies)

    def show(self):
        self.preview()
        self.panel.show()
        self.panel.raise_()

    def _status(self):
        active = self.engine.active
        label = 'Desktop Assistance · Active' if active else 'Desktop Assistance · Off'
        self.indicator.setText(label)
        self.panel.status.setText(label + (' · Stop at any time. Sources: ' + ', '.join(self.engine.session.enabled_sources) if active else
            ' · No background context reads.'))
        if self.session_action:
            self.session_action.setText('Stop Desktop Assistance' if active else 'Start Desktop Assistance…')
        if hasattr(self.owner, 'refresh_ui_state'):
            self.owner.refresh_ui_state()

    def request_capture(self):
        dialog = ContextPermissionDialog(self.permissions, self.panel)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected()
        grants = set(self.permissions.grants) | set(selected)
        self.permissions.grant_session(grants)
        self.clear(revoke=False)
        self.delayed_sources = selected
        self.panel.status.setText('Switch to the target application. Capture starts in 3 seconds…')
        self.capture_timer.start(3000)

    def _delayed_capture(self):
        self.capture(once=self.delayed_sources)
        self.delayed_sources = ()

    def capture(self, *, once=(), assistance=False):
        if self.busy or self.owner.quitting:
            return
        self.invalidate_consumers()
        if assistance and not self.engine.active:
            return
        requested = self.engine.session.enabled_sources if assistance else None
        job = _CaptureJob(self.service, once, self.owner.capture_created_at if self.owner.image_bytes else None, requested)
        self.worker = job
        job.signals.finished.connect(self.finished, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    @Slot(object, object, str)
    def finished(self, job, snapshot, error):
        if job is not self.worker:
            return
        self.worker = None
        if self.owner.quitting or job.cancelled.is_set():
            return
        if error:
            self.panel.preview.setPlainText(error)
        elif snapshot and self.service.latest is snapshot:
            self.preview()
            if self.engine.active:
                self.evaluate()
        self._status()

    def preview(self):
        if self.service.latest:
            try:
                self.panel.set_sources(self.service.sources(self.service.latest.snapshot_id, check_window=False),
                    self.service.latest.warnings)
                return
            except ValueError:
                pass
        self.panel.set_sources(())

    def invalidate_consumers(self):
        owner = self.owner
        if getattr(owner, 'automation_controller', None):
            owner.automation_controller.invalidate()
        if getattr(owner, 'desktop_actions_controller', None):
            owner.desktop_actions_controller.stop()
        if owner.context_session is not None and owner.context_session.desktop_sources:
            owner.context_permissions.select_desktop_sources(owner.context_session, ())
            owner.context_latest_request = None
            if owner.context_dialog:
                owner.context_dialog.set_desktop_sources(())
                if owner.context_dialog._busy:
                    owner.context_dialog.show_error('Desktop Context changed. Review sources and ask again.')
        agent = getattr(owner, 'agent_controller', None)
        if agent and agent.service:
            with agent.store.lock:
                for session_id in tuple(agent.store.temporary_sessions - agent.store.released_sessions):
                    if session_id == agent.session_id:
                        agent.cancel()
                    else:
                        agent.service.cancel(session_id)
                    agent.service.contexts.pop(session_id, None)
                    agent.store.clear_temporary(session_id)
                    if session_id == agent.session_id:
                        agent.refresh()
        self.engine.pending.clear()
        self.banner.hide()
        self.result_banner.hide()

    def clear(self, *, revoke=True):
        self.capture_timer.stop()
        self.delayed_sources = ()
        if self.worker:
            self.worker.cancelled.set()
        self.service.clear(revoke=revoke)
        self.invalidate_consumers()
        self.banner.hide()
        self.preview()

    def remove(self, source_type):
        self.service.remove_source(source_type)
        self.invalidate_consumers()
        self.preview()

    def sources(self):
        snapshot = self.service.latest
        if not snapshot:
            raise ValueError('Capture and review Desktop Context first.')
        return self.service.sources(snapshot.snapshot_id)

    def open_assistant(self):
        try:
            sources = self.sources()
            self.owner.open_context_assistant(desktop=True)
            if self.owner.context_session and self.owner.context_dialog:
                self.owner.context_permissions.select_desktop_sources(self.owner.context_session, sources)
                self.owner.context_dialog.set_desktop_sources(sources)
                self.owner.context_dialog.include_desktop.setChecked(True)
        except ValueError as error:
            self.panel.preview.setPlainText(str(error))

    def open_agent(self):
        try:
            self.sources()
            self.owner.open_agent()
            self.owner.agent_controller.page.include_desktop.setChecked(True)
        except ValueError as error:
            self.panel.preview.setPlainText(str(error))

    def open_settings(self):
        dialog = QDialog(self.panel)
        self.settings_dialog = dialog
        dialog.setWindowTitle('Desktop Context Permissions')
        dialog.resize(720, 620)
        layout = QVBoxLayout(dialog)
        widget = DesktopPolicyWidget(self.owner.config)
        layout.addWidget(widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                config = replace(self.owner.config, **widget.values())
                save_config(config, self.owner.paths.config_file)
                self.owner.config = self.owner.main_window.config = config
                self.apply_preferences()
            except (OSError, ValueError) as error:
                QMessageBox.warning(self.panel, 'Desktop Context settings', str(error))
        self.settings_dialog = None

    def request_start(self):
        if self.engine.active:
            self.stop()
            return
        dialog = ContextPermissionDialog(self.permissions, self.panel, assistance=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.start(duration_minutes=dialog.duration.currentData(),
                    enabled_sources=('metadata', *dialog.selected()), suggestion_level=dialog.level.currentData(),
                    allowed_apps=tuple(x.strip() for x in dialog.allowed_apps.text().split(',') if x.strip()),
                    allow_fullscreen=dialog.fullscreen.isChecked())
            except (PermissionError, ValueError) as error:
                QMessageBox.warning(self.panel, 'Desktop Assistance', str(error))

    def start(self, **preferences):
        if self.owner.safe_mode:
            raise PermissionError('Safe Mode disables Desktop Assistance.')
        self.stop()
        self.engine.start(**preferences)
        self.permissions.grant_session(s for s in self.engine.session.enabled_sources if s != 'metadata')
        if set(self.engine.session.enabled_sources) & {'ui', 'selection'}:
            from src.desktop_context.events import FocusEventObserver
            self.focus_events = FocusEventObserver()
            self.focus_events.start()
        self.detector.reset()
        self.timer.start()
        self._status()

    def stop(self):
        self.engine.stop()
        self.focus_events.stop()
        self.focus_pending_since = None
        self.timer.stop()
        self.detector.reset()
        self.clear()
        self._status()

    def tick(self):
        if not self.engine.active or self.owner.quitting:
            self.stop()
            return
        self.engine.expire()
        if self.banner.suggestion_id not in self.engine.pending:
            self.banner.hide()
            self.result_banner.hide()
        if self.busy or self.capture_timer.isActive():
            return
        if self.focus_events.consume():
            self.focus_pending_since = time.monotonic()
        window = self.foreground()
        if not window.hwnd or window.process_id == os.getpid() or self.permissions.is_blocked(window.process_name):
            return
        allowed_apps = self.engine.session.allowed_apps
        from src.desktop_context.permissions import app_key
        if allowed_apps and app_key(window.process_name) not in allowed_apps:
            return
        focus_changed = (self.focus_pending_since is not None and time.monotonic() - self.focus_pending_since >= 2
            and time.monotonic() - self.last_refresh >= 10)
        if self.detector.changed(window) or focus_changed:
            self.last_refresh = time.monotonic()
            self.focus_pending_since = None
            self.capture(assistance=True)
        self._status()

    def evaluate(self, *, screenshot_skill=None, screenshot_id=None):
        if not self.engine.active:
            return
        snapshot = self.service.latest
        if snapshot is None:
            return
        try:
            self.service.validate(snapshot.snapshot_id)
            from src.desktop_context.models import ActiveWindowContext
            safety = self.foreground()
            # When SnapFlow is foreground, the captured target safety metadata is authoritative.
            if safety.process_id == os.getpid():
                safety = self.service.safety_window
            if safety is None or self.permissions.is_blocked(safety.process_name):
                return
            suggestions = self.engine.evaluate(snapshot, screenshot_skill=screenshot_skill,
                screenshot_id=screenshot_id, safety_window=safety)
        except ValueError:
            return
        if suggestions:
            self.banner.show_suggestion(suggestions[0])
            self.result_banner.show_suggestion(suggestions[0])

    def accept(self, suggestion_id):
        try:
            # Opening Mission Control only. No planning network call or execution.
            self.sources()
            suggestion = self.engine.pending.get(suggestion_id)
            if not suggestion or not self.service.latest or suggestion.source_ids != (self.service.latest.snapshot_id,):
                raise ValueError('Suggestion context changed.')
            goal = self.engine.accept(suggestion_id)
            self.owner.open_agent(goal)
            self.owner.agent_controller.page.include_desktop.setChecked(True)
        except ValueError:
            pass
        self.banner.hide()
        self.result_banner.hide()

    def dismiss(self, suggestion_id):
        self.engine.dismiss(suggestion_id)
        self.banner.hide()
        self.result_banner.hide()
