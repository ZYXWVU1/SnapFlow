"""Visible consent and session controls. Closing always cancels temporary evidence."""
import os
from uuid import uuid4
from PySide6.QtCore import Qt, QTimer, Signal, QThreadPool
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget

from src.automation.recorder import DemonstrationRecorder
from src.automation.observer import WindowsDemonstrationObserver
from src.desktop_actions.models import DesktopActionFence


class TeachDialog(QDialog):
    draft_ready = Signal(object)
    active_changed = Signal(bool)

    def __init__(self, owner, *, enabled):
        super().__init__(owner.main_window, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.owner = owner
        self.setWindowTitle('Teach SnapFlow a Task')
        self.resize(510, 400)
        self.feature_enabled = enabled
        self.permission_version = None
        self.recorder = DemonstrationRecorder(enabled=self.allowed)
        self.observer = WindowsDemonstrationObserver(self.recorder)
        self.fences = []
        self.completed = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.checkpoint_job = None
        layout = QVBoxLayout(self)
        self.status = QLabel('Choose the task and review the application scope before starting.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.goal = QLineEdit()
        self.goal.setPlaceholderText('What task will you demonstrate?')
        self.goal.setMaxLength(200)
        layout.addWidget(self.goal)
        layout.addWidget(QLabel('Scope: the reviewed Desktop Context application only.\nAdd another reviewed window explicitly for a cross-app task.'))
        self.scope = QListWidget()
        layout.addWidget(self.scope)
        self.add = QPushButton('Use / Add Reviewed Desktop Context Window')
        self.add.clicked.connect(self.add_window)
        layout.addWidget(self.add)
        self.checkpoint_button = QPushButton('Capture State Checkpoint')
        self.checkpoint_button.setEnabled(False)
        self.checkpoint_button.clicked.connect(self.checkpoint)
        layout.addWidget(self.checkpoint_button)
        layout.addWidget(QLabel('Records: UI Automation actions and safe control state changes.\nClipboard, keystrokes, coordinates and screenshots: disabled.\nProtected fields are unsupported. Events remain temporary in memory.'))
        row = QHBoxLayout()
        self.start_button = QPushButton('Start Teaching')
        self.start_button.clicked.connect(self.start)
        self.pause_button = QPushButton('Pause')
        self.pause_button.clicked.connect(self.pause)
        self.finish_button = QPushButton('Finish and Review')
        self.finish_button.clicked.connect(self.finish)
        cancel = QPushButton('Cancel and Discard')
        cancel.clicked.connect(self.reject)
        for button in (self.start_button, self.pause_button, self.finish_button, cancel):
            row.addWidget(button)
        layout.addLayout(row)
        self.pause_button.setEnabled(False)
        self.finish_button.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.add_window()

    def add_window(self):
        try:
            service = self.owner.desktop_controller.service
            snapshot = service.latest
            if not snapshot or not snapshot.active_window:
                raise ValueError('Capture and review the target in Desktop Context first.')
            service.validate(snapshot.snapshot_id, check_window=False)
            window = snapshot.active_window
            if window.process_id == os.getpid() or not window.process_name:
                raise ValueError('Choose an application other than SnapFlow.')
            if len(self.fences) >= 4:
                raise ValueError('At most four application windows are supported.')
            if any(f.allowed_window == window.hwnd for f in self.fences):
                return
            self.fences.append(DesktopActionFence(window.process_id, window.process_name, window.hwnd, 'teach_' + uuid4().hex))
            self.scope.addItem(window.process_name + ' · ' + (window.window_title or 'Reviewed window'))
            self.status.setText('Scope ready. Start Teaching explicitly, then switch to the selected application.')
        except Exception as exc:
            self.status.setText(str(exc) if isinstance(exc, ValueError) else 'Desktop Context permission is unavailable. Review the window first.')

    def start(self):
        try:
            self.permission_version = self.owner.desktop_controller.service.permissions.version
            self.recorder.start(self.goal.text().strip(), tuple(self.fences))
            self.observer.start(self.fences)
            self.active_changed.emit(True)
            self.status.setText('TEACH MODE ACTIVE · Connecting to the selected applications…')
            self.goal.setEnabled(False)
            self.add.setEnabled(False)
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(True)
            self.checkpoint_button.setEnabled(True)
            self.timer.start()
        except Exception:
            self.observer.close()
            self.recorder.cancel()
            self.status.setText('Teaching could not start. Check the task, scope, Beta feature and Safe Mode.')

    def allowed(self):
        permissions = self.owner.desktop_controller.service.permissions
        return self.feature_enabled() and (self.permission_version is None or self.permission_version == permissions.version) and all(
            not permissions.is_blocked(f.allowed_process_name) for f in self.fences)

    def tick(self):
        try:
            self.observer.drain()
            if self.recorder.session is None:
                self.reject()
                return
            if self.recorder.session.incomplete:
                raise RuntimeError('Incomplete teaching trace.')
            ready = self.observer.ready == len(self.fences)
            self.finish_button.setEnabled(ready and self.checkpoint_job is None)
            if ready:
                status = self.recorder.session.status
                self.status.setText(('TEACH MODE ACTIVE' if status == 'recording' else 'TEACH MODE PAUSED') +
                    ' · ' + str(len(self.recorder.session.events)) + ' semantic changes · Only the selected applications')
        except Exception:
            self.recorder.pause()
            self.observer.close()
            self.timer.stop()
            self.status.setText('TEACH MODE PAUSED · Observation unavailable or permission revoked. Cancel and teach again.')
            self.finish_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.checkpoint_button.setEnabled(False)

    def pause(self):
        if not self.recorder.session:
            return
        if self.recorder.session.status == 'recording':
            self.recorder.pause()
            self.observer.close()
            self.pause_button.setText('Resume')
            self.status.setText('TEACH MODE PAUSED · Native subscriptions removed.')
        else:
            try:
                self.recorder.resume()
                self.observer.start(self.fences)
                self.pause_button.setText('Pause')
            except Exception:
                self.reject()

    def checkpoint(self):
        if self.checkpoint_job:
            return
        from src.ui.agent.desktop_actions import _Job
        self.status.setText('Switch to a scoped application within 15 seconds. One bounded state checkpoint will be captured.')
        self.checkpoint_button.setEnabled(False)
        job = self.checkpoint_job = _Job(lambda cancelled: self.observer.checkpoint(), None)
        def finished(job, result, error):
            self.checkpoint_job = None
            self.checkpoint_button.setEnabled(True)
            if error:
                self.status.setText('Checkpoint unavailable. Select the target application and try again.')
        job.signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    def finish(self):
        try:
            self.observer.drain()
            session = self.recorder.finish()
            self.observer.close()
            self.timer.stop()
            from src.automation.compiler import DemonstrationInterpreter
            skill = DemonstrationInterpreter().compile(session)
            session.events.clear()
            self.completed = True
            self.active_changed.emit(False)
            self.draft_ready.emit(skill)
            self.accept()
        except Exception as exc:
            self.status.setText(str(exc) if isinstance(exc, ValueError) else 'Could not finish teaching. Cancel and teach again.')

    def done(self, result):
        self.timer.stop()
        self.observer.close()
        self.recorder.cancel()
        self.pool.waitForDone(3000)
        self.active_changed.emit(False)
        super().done(result)
