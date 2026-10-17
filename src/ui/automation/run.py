"""Read-only preview followed by a fresh human decision for each desktop action."""
import json
import time
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QLabel, QPushButton, QListWidget, QHBoxLayout

from src.ui.agent.desktop_actions import _Job
from .editor import scalar


class AutomationRunDialog(QDialog):
    def __init__(self, controller, skill, *, testing=False):
        super().__init__(controller.owner.main_window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller, self.skill, self.testing = controller, skill, testing
        self.run = self.preview = None
        self.previews = ()
        self.started = time.perf_counter()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.worker = None
        self.setWindowTitle(('Test' if testing else 'Run') + ' Automation Skill · ' + skill.name)
        self.resize(700, 540)
        layout = QVBoxLayout(self)
        self.status = QLabel('Review the Desktop Actions window fence, enter inputs, then Dry Run. Every effect requires approval.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.inputs = {}
        for spec in skill.inputs:
            if spec.value_type in ('boolean', 'enum'):
                widget = QComboBox()
                widget.addItems(['true', 'false'] if spec.value_type == 'boolean' else list(spec.enum_values))
            else:
                widget = QLineEdit('' if spec.default_value is None else str(spec.default_value))
                widget.setMaxLength(spec.max_length)
                if spec.sensitive:
                    widget.setEchoMode(QLineEdit.EchoMode.Password)
            self.inputs[spec.input_id] = widget
            form.addRow(spec.label, widget)
        layout.addLayout(form)
        self.steps = QListWidget()
        for step in skill.steps:
            self.steps.addItem(step.title + ' · ' + step.action_type + ' · ' + step.risk_level)
        layout.addWidget(self.steps)
        row = QHBoxLayout()
        fence = QPushButton('Review Window Fence')
        fence.clicked.connect(controller.owner.desktop_actions_controller.show_settings)
        self.dry = QPushButton('Dry Run')
        self.dry.clicked.connect(self.dry_run)
        self.review = QPushButton('Review Next Action')
        self.review.clicked.connect(self.review_next)
        self.approve = QPushButton('Approve Once and Execute')
        self.approve.clicked.connect(self.execute)
        self.approve.setEnabled(False)
        self.review.setEnabled(False)
        stop = QPushButton('Stop')
        stop.clicked.connect(self.reject)
        for button in (fence, self.dry, self.review, self.approve, stop):
            row.addWidget(button)
        layout.addLayout(row)

    def job(self, operation, callback):
        if self.worker:
            return
        job = self.worker = _Job(operation, callback)
        self.dry.setEnabled(False)
        self.review.setEnabled(False)
        self.approve.setEnabled(False)
        def finished(job, result, error):
            if self.worker is not job:
                return
            self.worker = None
            if not job.cancelled.is_set():
                if error:
                    self.status.setText('Operation paused. Check permissions, the window fence and target. Repair a broken target in a new draft.')
                    if self.run:
                        self.run.status = 'paused'
                else:
                    callback(result)
        job.signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.pool.start(job)

    def dry_run(self):
        try:
            values = {}
            for spec in self.skill.inputs:
                widget = self.inputs[spec.input_id]
                text = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
                values[spec.input_id] = scalar(text) if spec.value_type in ('number', 'boolean') else text
                widget.setEnabled(False)
            self.run = self.controller.runtime.prepare(self.skill, values)
            def ready(previews):
                self.previews = list(previews)
                for i, preview in enumerate(previews):
                    self.steps.item(i).setText(self.skill.steps[i].title + ' · ' + preview.resolution.status + ' · ' + preview.risk_level)
                self.review.setEnabled(bool(previews and previews[0].allowed))
                self.status.setText('Dry run made no changes. Review the next action.' if all(p.allowed for p in previews) else
                    'Some later targets are unresolved. Review the window fence at each application transition. Broken targets require repair.')
            self.job(lambda cancelled: self.controller.runtime.dry_run(self.run), ready)
        except Exception as exc:
            self.status.setText(str(exc))

    def review_next(self):
        def ready(preview):
            self.preview = preview
            self.approve.setEnabled(preview.allowed)
            self.review.setEnabled(not preview.allowed and self.run.status == 'ready')
            if preview.allowed:
                self.previews[self.run.index] = preview
            element = preview.resolution.resolved_element
            self.status.setText('Target: ' + (element.name if element else 'Unresolved') + '\nAction: ' + preview.request.action_type +
                '\nRisk: ' + preview.risk_level + '\nCurrent value: ' + str(element.state.get('value', ''))[:4096] if element else 'Unresolved target')
            if preview.allowed:
                self.status.setText(self.status.text() + '\nDesired value: ' + str(preview.request.value)[:4096] +
                    '\nOutcome: ' + ('; '.join(c.property + ' = ' + str(c.equals) for c in preview.request.expected_postconditions) or 'Fresh requested state must verify.') +
                    '\nAfter approving, switch to the target application within 15 seconds.')
                self.controller.owner.desktop_actions_controller.highlight(element)
        self.job(lambda cancelled: self.controller.runtime.preview(self.run), ready)

    def execute(self):
        try:
            token = self.controller.runtime.service.approve(self.preview)
            def operation(cancelled):
                self.controller.owner.desktop_actions_controller.wait_for_target(cancelled)
                return self.controller.runtime.execute_next(self.run, token, cancelled=cancelled)
            def ready(result):
                self.controller.owner.desktop_actions_controller.clear_outlines()
                self.status.setText(result.status + ' · ' + (result.safe_error or 'Fresh UI state verified.'))
                if self.run.status == 'completed':
                    self.controller.store.record_usage(self.run, (time.perf_counter() - self.started) * 1000)
                    if self.testing:
                        try:
                            self.controller.store.record_test(self.run, self.previews)
                            self.status.setText('Verified test recorded for this version. You can now publish from the editor.')
                        except Exception as exc:
                            self.status.setText(str(exc))
                elif self.run.status == 'ready':
                    self.review.setEnabled(True)
            self.job(operation, ready)
        except Exception:
            self.status.setText('Approval expired or the action changed. Close and review a new run.')

    def done(self, result):
        if self.worker:
            self.worker.cancelled.set()
        if self.run:
            if self.run.status != 'completed':
                self.controller.runtime.stop(self.run)
            self.run.inputs = {}
            self.run.requests.clear()
        self.controller.owner.desktop_actions_controller.clear_outlines()
        self.pool.waitForDone(10000)
        if self.run:
            service = self.controller.runtime.service
            for key, preview in tuple(service.previews.items()):
                if preview.request.action_id.startswith(self.run.run_id + '_'):
                    service.previews.pop(key, None)
            for key, preview in tuple(service.approvals.items()):
                if preview.request.action_id.startswith(self.run.run_id + '_'):
                    service.approvals.pop(key, None)
        for widget in self.inputs.values():
            widget.clear()
        self.preview, self.previews, self.run = None, (), None
        super().done(result)
