"""Manual browser procedure runner; exact action approval stays in the sidebar."""
import time

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
                               QLabel, QPushButton, QListWidget, QHBoxLayout)

from src.ui.agent.desktop_actions import _Job
from src.ui.automation.editor import scalar


class WebAutomationRunDialog(QDialog):
    def __init__(self, controller, skill, *, testing=False):
        super().__init__(controller.owner.main_window)
        self.controller, self.skill, self.testing = controller, skill, testing
        self.runtime = controller.owner.browser_controller.web_runtime()
        self.run = self.preview = self.worker = None
        self.previews = [None] * len(skill.steps)
        self.started = time.perf_counter()
        self.closed = self.usage_recorded = False
        # A child-owned pool's destructor waits for work. Closing this dialog
        # must instead signal cancellation and return while the gateway unwinds.
        self.pool = QThreadPool.globalInstance()
        self.setWindowTitle(('Test' if testing else 'Run') + ' Website Procedure · ' + skill.name)
        self.resize(780, 570)
        layout = QVBoxLayout(self)
        scope = QLabel('Website scope: ' + ', '.join(skill.supported_origins))
        scope.setTextFormat(Qt.TextFormat.PlainText)
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.status = QLabel('Assist the website and allow controlled interactions. Dry Run checks the reviewed inventory. '
                             'Each queued effect still needs your exact approval in the browser sidebar.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.inputs = {}
        for spec in skill.inputs:
            if spec.value_type in ('boolean', 'enum'):
                widget = QComboBox()
                widget.addItems(['true', 'false'] if spec.value_type == 'boolean' else list(spec.enum_values))
                if spec.default_value is not None:
                    widget.setCurrentText(str(spec.default_value).lower() if spec.value_type == 'boolean' else str(spec.default_value))
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
            self.steps.addItem(step.title + ' · ' + step.action_type)
        layout.addWidget(self.steps)
        row = QHBoxLayout()
        website = QPushButton('Review website')
        website.clicked.connect(controller.owner.browser_controller.show)
        self.dry = QPushButton('Dry Run')
        self.dry.clicked.connect(self.dry_run)
        self.queue = QPushButton('Queue next for sidebar approval')
        self.queue.clicked.connect(self.execute)
        self.rebind = QPushButton('Use newly reviewed page')
        self.rebind.clicked.connect(self.rebind_page)
        stop = QPushButton('Stop')
        stop.clicked.connect(self.reject)
        for button in (website, self.dry, self.rebind):
            row.addWidget(button)
        layout.addLayout(row)
        execution = QHBoxLayout()
        execution.addWidget(self.queue)
        execution.addWidget(stop)
        layout.addLayout(execution)
        self.manual = QPushButton('I completed this step manually')
        self.manual.clicked.connect(self.confirm_manual)
        layout.addWidget(self.manual)
        self._update_controls()

    def _current(self):
        return self.skill.steps[self.run.index] if self.run and self.run.index < len(self.skill.steps) else None

    def _update_controls(self):
        active = not self.closed and self.worker is None and self.controller.enabled()
        current = self._current()
        ready = bool(active and self.run and self.run.status == 'ready' and not self.run.stopped.is_set())
        available = bool(current and self.preview and self.preview.allowed)
        self.dry.setEnabled(bool(active and (self.run is None or self.run.status in ('ready', 'paused'))))
        self.queue.setEnabled(bool(ready and available and current.executable))
        self.manual.setVisible(bool(current and not current.executable))
        self.manual.setEnabled(bool(ready and available and not current.executable))
        self.rebind.setEnabled(bool(active and current and self.run.status == 'paused'
            and self.run.index not in self.run.requests and not self.run.stopped.is_set()))
        for widget in self.inputs.values():
            widget.setEnabled(bool(active and self.run is None))

    def _job(self, operation, callback):
        if self.closed or self.worker is not None:
            return
        job = self.worker = _Job(operation, callback)
        job.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._update_controls()
        self.pool.start(job)

    @Slot(object, object, object)
    def _finished(self, job, result, error):
        if self.worker is not job:
            return
        self.worker = None
        if self.closed or job.cancelled.is_set():
            # A cancelled first dry run may finish after the dialog closes.
            if isinstance(result, tuple) and result and hasattr(result[0], 'skill'):
                self.runtime.stop(result[0])
                result[0].inputs = {}
            if self.closed:
                self.deleteLater()
            return
        try:
            if error:
                if self.run and self.run.status not in ('completed', 'interrupted', 'stopped'):
                    self.run.status = 'interrupted' if self.run.index in self.run.requests else 'paused'
                self.status.setText('Procedure paused. Review website authority and the target. '
                    'An interrupted or uncertain attempted action cannot be retried automatically.')
            else:
                job.callback(result)
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self._update_controls()

    def _show_previews(self, start, previews):
        for offset, preview in enumerate(previews):
            index = start + offset
            self.previews[index] = preview
            self.steps.item(index).setText(self.skill.steps[index].title + ' · ' +
                ('Ready' if preview.allowed else 'Paused') + ' · ' + preview.target)
        self.preview = self.previews[self.run.index] if self._current() else None
        if self.preview:
            step = self._current()
            self.status.setText('Target: ' + self.preview.target + '\n' +
                ('Complete this step yourself on the website, then confirm below.' if not step.executable else
                 'Proposed change: ' + str(self.preview.proposed)[:1200] +
                 '\nQueue this action, then review and approve its exact current/proposed state in the browser sidebar.') +
                ('\n' + self.preview.reason if self.preview.reason else ''))

    def dry_run(self):
        if self.closed or self.worker or self.run and self.run.status not in ('ready', 'paused'):
            return
        if self.run is None:
            try:
                session_id = self.controller.owner.browser_controller.selected_browser_session()
            except Exception as exc:
                self.status.setText(str(exc))
                return
            values = {}
            for spec in self.skill.inputs:
                widget = self.inputs[spec.input_id]
                text = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
                values[spec.input_id] = scalar(text) if spec.value_type in ('number', 'boolean') else text
            def operation(cancelled):
                run = self.runtime.prepare(self.skill, values, session_id=session_id)
                values.clear()
                if cancelled():
                    self.runtime.stop(run)
                    run.inputs = {}
                    return run, ()
                return run, self.runtime.dry_run(run)
            def ready(result):
                self.run, previews = result
                self.dry.setText('Refresh preview')
                self._show_previews(self.run.index, previews)
            self._job(operation, ready)
        else:
            run, index = self.run, self.run.index
            self._job(lambda cancelled: self.runtime.dry_run(run),
                      lambda previews: self._show_previews(index, previews))

    def execute(self):
        current = self._current()
        if not current or not current.executable:
            return  # No generic execute path can attest a human checkpoint.
        self._execute(False)

    def confirm_manual(self):
        current = self._current()
        if not current or current.executable:
            return
        self._execute(True)

    def _execute(self, manual_confirmed):
        if (self.closed or self.worker or not self.run or self.run.status != 'ready'
                or not self.preview or not self.preview.allowed):
            return
        run, index = self.run, self.run.index
        self.status.setText('Waiting for your exact approval in the browser sidebar. Stop cancels this request.'
                            if not manual_confirmed else 'Recording your manual checkpoint confirmation.')
        def operation(cancelled):
            preview = self.runtime.preview(run)
            result = self.runtime.execute_next(run, manual_confirmed=manual_confirmed, cancelled=cancelled,
                guard=lambda: not self.closed and self.controller.enabled())
            return preview, result
        def ready(result):
            preview, outcome = result
            self.previews[index] = preview
            self.preview = None
            self.steps.item(index).setText(self.skill.steps[index].title + ' · ' + outcome.get('status', 'unknown'))
            if run.status == 'completed':
                self._record_usage()
                if self.testing:
                    self.controller.store.record_test(run, self.previews)
                self.status.setText('Verified test recorded. Return to the editor to publish this version.' if self.testing else
                                    'Procedure completed. Requested control states were verified; manual steps were confirmed by you.')
            elif run.status == 'ready':
                self.status.setText('Step complete. Refresh the preview before queuing the next step.')
            elif run.status == 'paused':
                self.status.setText('Review and Assist the destination page, allow interactions, then use the newly reviewed page.')
            else:
                self.status.setText('Outcome is uncertain or interrupted. Inspect the website and Recovery. This run cannot replay the action.')
        self._job(operation, ready)

    def rebind_page(self):
        if self.closed or self.worker or not self.run or self.run.status != 'paused' or self.run.index in self.run.requests:
            return
        run = self.run
        try:
            # The session provider reads desktop review widgets. Rebinding is
            # local validation only, so obtain it on this thread before work.
            self.runtime.rebind(run)
        except Exception as exc:
            self.status.setText(str(exc))
            self._update_controls()
            return
        self._job(lambda cancelled: self.runtime.dry_run(run),
                  lambda previews: self._show_previews(run.index, previews))

    def _record_usage(self):
        if self.run and not self.usage_recorded:
            self.controller.store.record_usage(self.run, (time.perf_counter() - self.started) * 1000)
            self.usage_recorded = True

    def done(self, result):
        if not self.closed:
            self.closed = True
            if self.worker:
                self.worker.cancelled.set()
            if self.run:
                if self.run.status != 'completed':
                    self.runtime.stop(self.run)
                try:
                    self._record_usage()
                except Exception:
                    pass
                self.run.inputs = {}
            for widget in self.inputs.values():
                widget.clear()
            self.preview, self.previews, self.run = None, [], None
            self._update_controls()
        super().done(result)
        if self.worker is None:
            self.deleteLater()
