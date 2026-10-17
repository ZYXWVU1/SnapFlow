"""Explicit window fence and exact desktop preview review, with bounded workers."""
from dataclasses import replace
import math
import time
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QTimer, QRectF
from PySide6.QtGui import QGuiApplication, QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QLabel, QCheckBox, QPushButton, QDialogButtonBox, QPlainTextEdit, QListWidget

from src.config import save_config
from src.desktop_actions.agent import DesktopActionCapabilityProvider
from src.desktop_actions.models import DesktopActionFence
from src.desktop_actions.native import WindowsDesktopActionProvider
from src.desktop_actions.service import DesktopActionService
from src.desktop_context.active_window import ActiveWindowProvider
from src.observability.performance import trace


class _Signals(QObject):
    finished = Signal(object, object, object)


class _Job(QRunnable):
    def __init__(self, operation, callback):
        super().__init__()
        self.operation, self.callback = operation, callback
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        result, error = None, None
        try:
            if not self.cancelled.is_set():
                result = self.operation(self.cancelled.is_set)
        except Exception:
            error = 'Desktop action review unavailable. Refresh the Desktop Context and permissions.'
        self.signals.finished.emit(self, result, error)


class TargetOutline(QWidget):
    def __init__(self, rect):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(rect)
        QTimer.singleShot(5000, self.close)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#315EEA'), 3))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 4, 4)


def overlay_rectangles(bounds):
    """Match Windows physical monitor origins to Qt logical screen geometry."""
    import ctypes
    from ctypes import wintypes as w
    from PySide6.QtCore import QRect
    user = ctypes.WinDLL('user32')
    class Info(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('monitor', w.RECT), ('work', w.RECT), ('flags', w.DWORD), ('device', w.WCHAR * 32)]
    user.GetMonitorInfoW.argtypes = (w.HANDLE, ctypes.POINTER(Info))
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)
    monitors = {}
    def visit(handle, hdc, rect, data):
        info = Info()
        info.size = ctypes.sizeof(info)
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.monitor
            monitors[info.device] = (r.left, r.top, r.right, r.bottom)
        return True
    callback = callback_type(visit)
    user.EnumDisplayMonitors.argtypes = (w.HDC, ctypes.POINTER(w.RECT), callback_type, w.LPARAM)
    user.EnumDisplayMonitors(None, None, callback, 0)
    result = []
    for screen in QGuiApplication.screens():
        physical = monitors.get(screen.name())
        if physical is None:
            continue
        l, t, r, b = max(bounds[0], physical[0]), max(bounds[1], physical[1]), min(bounds[2], physical[2]), min(bounds[3], physical[3])
        if l >= r or t >= b:
            continue
        ratio, logical = screen.devicePixelRatio(), screen.geometry()
        result.append(QRect(round(logical.x() + (l - physical[0]) / ratio),
            round(logical.y() + (t - physical[1]) / ratio), math.ceil((r - l) / ratio), math.ceil((b - t) / ratio)))
    return result


class DesktopActionsController(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.service = DesktopActionService(WindowsDesktopActionProvider(), owner.execution_journal,
            enabled=lambda: not owner.quitting and owner.config.controlled_desktop_actions_enabled and
                owner.beta_features.enabled('controlled_computer_use_v1'),
            private_mode=lambda: owner.config.private_mode)
        self.provider = DesktopActionCapabilityProvider(self.service, owner.desktop_controller.service)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.worker, self.dialog, self.outlines = None, None, []
        QGuiApplication.instance().screenAdded.connect(self.clear_outlines)
        QGuiApplication.instance().screenRemoved.connect(self.clear_outlines)

    def _start(self, operation, callback):
        if self.worker is not None:
            return
        job = self.worker = _Job(operation, callback)
        job.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    def _finished(self, job, result, error):
        if job is not self.worker:
            return
        self.worker = None
        if not job.cancelled.is_set():
            job.callback(result, error)

    def clear_outlines(self, *args):
        for outline in self.outlines:
            outline.close()
            outline.deleteLater()
        self.outlines.clear()

    def highlight(self, element):
        self.clear_outlines()
        if not self.owner.config.desktop_target_highlight or not element or not element.bounds:
            return
        with trace('desktop_overlay', 'desktop_actions'):
            for rect in overlay_rectangles(element.bounds):
                outline = TargetOutline(rect)
                self.outlines.append(outline)
                outline.show()

    def stop(self):
        if self.worker:
            self.worker.cancelled.set()
        self.provider.stop()
        self.clear_outlines()
        if self.dialog:
            self.dialog.reject()

    def show_settings(self):
        dialog = QDialog(self.owner.main_window)
        self.dialog = dialog
        dialog.setWindowTitle('Desktop Actions')
        dialog.resize(640, 450)
        layout = QVBoxLayout(dialog)
        note = QLabel('Allow Agent actions only in the reviewed application window. Structured UI Automation is used. External commits always ask; destructive and protected actions are disabled. Input fallback is unavailable in this beta.')
        note.setWordWrap(True)
        layout.addWidget(note)
        enabled = QCheckBox('Enable Controlled Desktop Actions')
        enabled.setChecked(self.owner.config.controlled_desktop_actions_enabled)
        layout.addWidget(enabled)
        highlight = QCheckBox('Highlight targets before approval')
        highlight.setChecked(self.owner.config.desktop_target_highlight)
        layout.addWidget(highlight)
        label = QLabel('Local Editing: Ask Each Action\nExternal Commit: Always Ask\nInput Fallback: Off\nDestructive Actions: Disabled\nWindow fence: expires after 10 minutes; approvals expire after 60 seconds.')
        label.setWordWrap(True)
        layout.addWidget(label)
        status = QLabel('Capture and review UI controls in Desktop Context before creating a fence.')
        status.setWordWrap(True)
        layout.addWidget(status)
        allow = QPushButton('Allow Agent actions in the reviewed window')
        layout.addWidget(allow)
        def begin():
            try:
                config = replace(self.owner.config, controlled_desktop_actions_enabled=enabled.isChecked(), desktop_target_highlight=highlight.isChecked())
                save_config(config, self.owner.paths.config_file)
                self.owner.config = self.owner.main_window.config = config
                if not self.owner.beta_features.enabled('controlled_computer_use_v1'):
                    status.setText('Enable Controlled Desktop Actions in Beta Features first.')
                    return
                snapshot = self.owner.desktop_controller.service.latest
                window = snapshot.active_window if snapshot else None
                if not window:
                    status.setText('Capture and review the target window first.')
                    return
                fence = DesktopActionFence(window.process_id, window.process_name, window.hwnd, uuid4().hex)
                allow.setEnabled(False)
                status.setText('Checking the reviewed window and available UI Automation patterns…')
                def ready(result, error):
                    allow.setEnabled(True)
                    status.setText(error or 'Window fence active. Create an Agent mission using this Desktop Context and allow the required writes. Each action still requires review.')
                self._start(lambda cancelled: self.provider.prepare(fence, snapshot, cancelled=cancelled), ready)
            except Exception:
                status.setText('Desktop action settings or target unavailable. Refresh context and permissions.')
        allow.clicked.connect(begin)
        stop = QPushButton('Stop Desktop Actions')
        stop.clicked.connect(lambda: (self.stop(), status.setText('Desktop actions stopped.')))
        layout.addWidget(stop)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            try:
                config = replace(self.owner.config, controlled_desktop_actions_enabled=enabled.isChecked(), desktop_target_highlight=highlight.isChecked())
                save_config(config, self.owner.paths.config_file)
                self.owner.config = self.owner.main_window.config = config
                if not enabled.isChecked():
                    self.stop()
                dialog.accept()
            except Exception:
                status.setText('Could not save Desktop Action settings.')
        buttons.accepted.connect(save)
        dialog.exec()
        self.dialog = None

    def preview_step(self, agent, step, inputs):
        sid = agent.session_id
        def ready(preview, error):
            if error or preview is None or agent.session_id != sid:
                agent.page.show_error(error or 'The Agent mission changed during review.')
                return
            dialog = QDialog(agent.page)
            self.dialog = dialog
            dialog.setWindowTitle('Desktop Action Preview · Approve Once')
            dialog.resize(620, 490)
            layout = QVBoxLayout(dialog)
            element = preview.resolution.resolved_element
            details = QLabel('Application: ' + (self.service.fence.allowed_process_name if self.service.fence else 'Unavailable') +
                '\nTarget: ' + (element.name if element else 'Unresolved') + '\nAction: ' + preview.request.action_type +
                '\nRisk: ' + preview.risk_level + '\nConfidence: ' + preview.resolution.status +
                '\n' + (preview.safe_error or 'After approval, select Execute and switch to the target window within 15 seconds.'))
            details.setTextFormat(Qt.TextFormat.PlainText)
            details.setWordWrap(True)
            layout.addWidget(details)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            current = element.state.get('value', '') if element else ''
            expected = '\n'.join(c.property + ' equals ' + str(c.equals)[:4096] for c in preview.request.expected_postconditions)
            text.setPlainText('Current value:\n' + str(current)[:4096] + '\n\nNew value:\n' + str(preview.request.value or '')[:4096] +
                '\n\nReviewed outcome:\n' + (expected or 'Fresh UI property must match the requested state.') +
                '\n\nPrior mission effects: review completed steps in Mission Control before approving a commit.')
            layout.addWidget(text)
            button = QPushButton('Highlight Target')
            button.clicked.connect(lambda: self.highlight(element))
            button.setEnabled(element is not None)
            layout.addWidget(button)
            if preview.resolution.candidate_count > 1:
                choices = QListWidget()
                for candidate in preview.resolution.candidates:
                    choices.addItem(candidate.name + ' · ' + candidate.automation_id)
                choices.currentRowChanged.connect(lambda index: self.highlight(preview.resolution.candidates[index]) if index >= 0 else None)
                layout.addWidget(choices)
            buttons = QDialogButtonBox()
            approve = buttons.addButton('Approve Once', QDialogButtonBox.ButtonRole.AcceptRole)
            approve.setEnabled(preview.allowed)
            approve.setDefault(False)
            buttons.addButton('Reject', QDialogButtonBox.ButtonRole.RejectRole)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            self.highlight(element)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    agent._guard(sid)
                    agent.service.approve_step(sid, step.step_id, inputs)
                    self.provider.approve(sid, step, inputs, preview)
                    agent.refresh()
                except Exception:
                    agent.page.show_error('The exact desktop action approval expired or changed. Review again.')
            self.clear_outlines()
            self.dialog = None
        self._start(lambda cancelled: self.provider.preview(sid, step, inputs), ready)
        agent.page.show_status('Resolving the target for a fresh Desktop Action Preview…')

    def wait_for_target(self, cancelled):
        fence = self.service._boundary()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cancelled() or self.service.cancelled.is_set():
                raise InterruptedError('Desktop actions stopped.')
            self.service._boundary(fence=fence)
            if ActiveWindowProvider(title_allowed=lambda app: False)().hwnd == fence.allowed_window:
                return
            time.sleep(.05)
        raise ValueError('The target application was not made active. Review before retrying.')
