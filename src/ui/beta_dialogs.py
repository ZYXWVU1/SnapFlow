"""Native beta controls and local feedback/health views behind Settings."""
from datetime import date
import json

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from src.beta.feedback import (CATEGORIES, COMPONENTS, EVENT_TYPES, FeedbackReport,
                               SafeDiagnostics, _ID, _RESOURCES, _number)
from src.observability.privacy import _SCHEMAS


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class BetaFeaturesDialog(QDialog):
    def __init__(self, service, parent=None, *, on_saved=None):
        super().__init__(parent)
        self.service, self.on_saved = service, on_saved
        self.setWindowTitle('Beta Features')
        self.resize(560, 450)
        root = QVBoxLayout(self)
        root.addWidget(_note('Beta features are optional and stay on this computer. Safe Mode suppresses them.'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll)
        self.flag_controls = {}
        for flag in service.registry.values():
            checkbox = QCheckBox(flag.title)
            checkbox.setAccessibleName(flag.title)
            checkbox.setChecked(service.configured(flag.id))
            body.addWidget(checkbox)
            body.addWidget(_note(flag.description + '\n' + flag.limitation + '\n' +
                                 ('Restart required.' if flag.restart_required else 'Takes effect immediately.')))
            self.flag_controls[flag.id] = checkbox
        self.experiments = QCheckBox('Participate in local UX experiments')
        self.experiments.setAccessibleName('Participate in local UX experiments')
        self.experiments.setChecked(service.experiments_enabled)
        body.addWidget(self.experiments)
        body.addWidget(_note('A random A/B label is assigned locally only when local reliability records are enabled. '
                             'Turning participation off removes the assignment. Permissions and privacy settings are never experiments.'))
        if service.warning:
            body.addWidget(_note(service.warning))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def save(self):
        try:
            self.service.save({key: widget.isChecked() for key, widget in self.flag_controls.items()},
                              experiments_enabled=self.experiments.isChecked())
        except (OSError, ValueError):
            QMessageBox.warning(self, 'Beta settings', 'Beta settings could not be saved. Check local folder permissions.')
            return
        if self.on_saved:
            self.on_saved()
        self.accept()


class NativeFeedbackDialog(QDialog):
    def __init__(self, service, parent=None, *, diagnostics=None, prefill=None, on_exported=None, on_outcome=None):
        super().__init__(parent)
        self.service, self.diagnostics, self.on_exported = service, diagnostics, on_exported
        self.on_outcome = on_outcome
        self._completed = self._cancelled = False
        self.rejected.connect(self._record_cancel)
        self.screenshot_path = None
        self.setWindowTitle('Send Feedback')
        self.resize(650, 700)
        root = QVBoxLayout(self)
        root.addWidget(_note('Write a report, then copy it or save a local package to share yourself. Nothing is uploaded.'))
        form = QFormLayout()
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        self.category.setAccessibleName('Feedback category')
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(160)
        self.title_edit.setAccessibleName('Feedback title')
        self.description_edit = QPlainTextEdit()
        self.description_edit.setAccessibleName('Feedback description')
        self.description_edit.setPlaceholderText('What happened? What did you expect? Steps to reproduce (up to 20,000 characters).')
        form.addRow('Category', self.category)
        form.addRow('Title', self.title_edit)
        form.addRow('Description', self.description_edit)
        root.addLayout(form)
        self._feedback_id = prefill.feedback_id if prefill is not None else None
        if prefill is not None:
            self.category.setCurrentText(prefill.category)
            self.title_edit.setText(prefill.title)
            self.description_edit.setPlainText(prefill.description)
        self.attach_diagnostics = QCheckBox('Attach the safe diagnostics shown below')
        self.attach_diagnostics.setAccessibleName('Attach safe diagnostics')
        self.attach_diagnostics.setEnabled(type(diagnostics) is SafeDiagnostics)
        root.addWidget(self.attach_diagnostics)
        self.diagnostics_preview = QPlainTextEdit()
        self.diagnostics_preview.setReadOnly(True)
        self.diagnostics_preview.setAccessibleName('Exact safe diagnostics attachment preview')
        self.diagnostics_preview.setMaximumHeight(170)
        self.diagnostics_preview.setPlainText(diagnostics.preview() if type(diagnostics) is SafeDiagnostics else 'Safe diagnostics are unavailable.')
        root.addWidget(self.diagnostics_preview)
        root.addWidget(_note('Diagnostics contain counts, timings, feature state and random error references. '
                             'They exclude logs, screenshots, Memory text, credentials, provider endpoints and model names. '
                             'Your own title and description are included verbatim.'))
        self.attach_screenshot = QCheckBox('Attach a screenshot I choose (PNG/JPG, up to 8 MiB)')
        self.attach_screenshot.setAccessibleName('Attach explicitly selected screenshot')
        root.addWidget(self.attach_screenshot)
        self.choose_screenshot = QPushButton('Choose image…')
        self.choose_screenshot.setEnabled(False)
        self.attach_screenshot.toggled.connect(self.choose_screenshot.setEnabled)
        self.choose_screenshot.clicked.connect(self.select_screenshot)
        root.addWidget(self.choose_screenshot)
        self.screenshot_status = QLabel('No screenshot selected.')
        root.addWidget(self.screenshot_status)
        row = QHBoxLayout()
        copy = QPushButton('Copy Feedback Report')
        copy.clicked.connect(self.copy_report)
        self.export_button = QPushButton('Save Feedback Package…')
        self.export_button.clicked.connect(self.export_package)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        for button in (copy, self.export_button, close):
            row.addWidget(button)
        root.addLayout(row)
        self.status = _note('')
        root.addWidget(self.status)

    def report(self):
        kwargs = {'feedback_id': self._feedback_id} if self._feedback_id else {}
        report = FeedbackReport(self.category.currentText(), self.title_edit.text(), self.description_edit.toPlainText(), **kwargs)
        self._feedback_id = report.feedback_id
        return report

    def copy_report(self):
        try:
            report = self.report()
        except ValueError as error:
            self.status.setText(str(error))
            return
        QApplication.clipboard().setText(json.dumps(report.payload(), indent=2, ensure_ascii=False))
        self.status.setText('Feedback report copied. Diagnostics and images are not copied.')

    def select_screenshot(self):
        source, _ = QFileDialog.getOpenFileName(self, 'Choose screenshot attachment', '', 'Images (*.png *.jpg *.jpeg)')
        if not source:
            return
        try:
            self.service.validate_screenshot(source)
        except (OSError, ValueError):
            self.status.setText('Choose a valid PNG or JPG image up to 8 MiB.')
            return
        self.screenshot_path = source
        self.screenshot_status.setText('An explicitly selected screenshot will be attached while the checkbox is on.')

    def export_package(self):
        try:
            report = self.report()
            if self.attach_screenshot.isChecked() and self.screenshot_path is None:
                raise ValueError('Choose an image before attaching a screenshot.')
        except ValueError as error:
            self.status.setText(str(error))
            if self.on_outcome:
                self.on_outcome('error')
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Save local feedback package',
            f'SnapFlow-Feedback-{report.feedback_id[:8]}.zip', 'ZIP packages (*.zip)')
        if not destination:
            return
        self.export_button.setEnabled(False)
        try:
            self.service.export(destination, report,
                diagnostics=self.diagnostics if self.attach_diagnostics.isChecked() else None,
                screenshot=self.screenshot_path if self.attach_screenshot.isChecked() else None)
        except (OSError, ValueError):
            self.status.setText('Export failed. Choose a new ZIP file outside app data and check folder permissions.')
            if self.on_outcome:
                self.on_outcome('error')
        else:
            self._completed = True
            self.status.setText('Local feedback package saved. Share it yourself through your preferred support channel.')
            if self.on_exported:
                self.on_exported(self.attach_diagnostics.isChecked())
            if self.on_outcome:
                self.on_outcome('completed')
        finally:
            self.export_button.setEnabled(True)

    def _record_cancel(self):
        if not self._completed and not self._cancelled and self.on_outcome:
            self._cancelled = True
            self.on_outcome('cancelled')


class _HealthSignals(QObject):
    result = Signal(object)
    failed = Signal()


class _HealthTask(QRunnable):
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.signals = _HealthSignals()

    def run(self):
        try:
            self.signals.result.emit(self.service.run())
        except Exception:
            self.signals.failed.emit()


class HealthCenterDialog(QDialog):
    def __init__(self, service, parent=None, *, resources=None, daily_metrics=None, recent_traces=None,
                 recent_errors=None, insights_callback=None, flags_callback=None, feedback_callback=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle('Diagnostics & Health Center')
        self.resize(730, 650)
        root = QVBoxLayout(self)
        root.addWidget(_note('Inspect local system, AI, storage, extensions and recovery state. '
                             'Checks make no provider requests and never replay interrupted actions.'))
        self.run_button = QPushButton('Run Health Check')
        self.run_button.clicked.connect(self.run_checks)
        root.addWidget(self.run_button)
        self.status = QLabel('No health check has run yet.')
        root.addWidget(self.status)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        self.health_preview = QPlainTextEdit()
        self.health_preview.setReadOnly(True)
        self.health_preview.setAccessibleName('Health checks and recommended recovery actions')
        tabs.addTab(self.health_preview, 'Health')
        self.advanced_preview = QPlainTextEdit()
        self.advanced_preview.setReadOnly(True)
        self.advanced_preview.setAccessibleName('Advanced safe performance and process metrics')
        self.advanced_preview.setPlainText(self._advanced(resources or {}, daily_metrics or [], recent_traces or []))
        tabs.addTab(self.advanced_preview, 'Advanced')
        self.errors_preview = QPlainTextEdit()
        self.errors_preview.setReadOnly(True)
        self.errors_preview.setAccessibleName('Recent safe error categories and random reference identifiers')
        safe_errors = []
        exception_types = {'ValueError', 'RuntimeError', 'TimeoutError', 'OSError', 'PermissionError',
            'ConnectionError', 'TypeError', 'KeyError', 'DatabaseError', 'OperationalError', 'MemoryError', 'Exception'}
        for row in (recent_errors or [])[:20]:
            if type(row) is not dict:
                continue
            reference, component, kind = row.get('crash_id'), row.get('component'), row.get('exception_type')
            if type(reference) is str and _ID.fullmatch(reference):
                safe_errors.append({'reference_id': reference,
                    'component': component if type(component) is str and component in COMPONENTS else 'application',
                    'exception_type': kind if type(kind) is str and kind in exception_types else 'Exception'})
        self.errors_preview.setPlainText(json.dumps(safe_errors, indent=2))
        tabs.addTab(self.errors_preview, 'Recent Errors')
        root.addWidget(_note('Before acknowledging an interrupted action, check the external service for its outcome. '
                             'Acknowledgement removes the review reminder; it does not run anything.'))
        row = QHBoxLayout()
        self.review_choice = QComboBox()
        self.review_choice.setAccessibleName('Interrupted action reference to acknowledge')
        self.acknowledge_button = QPushButton('Acknowledge selected review')
        self.acknowledge_button.clicked.connect(self.acknowledge_review)
        row.addWidget(self.review_choice, 1)
        row.addWidget(self.acknowledge_button)
        root.addLayout(row)
        shortcuts = QHBoxLayout()
        for text, callback in (('Beta Insights', insights_callback), ('Beta Features', flags_callback), ('Send Feedback', feedback_callback)):
            if callback is not None:
                button = QPushButton(text)
                button.clicked.connect(lambda _checked=False, action=callback: action())
                shortcuts.addWidget(button)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        shortcuts.addWidget(close)
        root.addLayout(shortcuts)
        self._refresh_reviews()

    @staticmethod
    def _advanced(resources, daily_metrics, traces):
        safe_resources = SafeDiagnostics(resources=tuple((key, value) for key, value in resources.items()
            if key in _RESOURCES and _number(value, 1000000))).payload()['process_resources']
        dimensions = set(_SCHEMAS['operation_completed'][1]['operation']) | set(_SCHEMAS['feature_visited'][1]['feature']) | {'', 'local', 'cloud'}
        metrics = []
        for row in daily_metrics[:1800]:
            if (type(row) is not dict or type(row.get('event_type')) is not str or row['event_type'] not in EVENT_TYPES
                    or type(row.get('dimension')) is not str or row['dimension'] not in dimensions):
                continue
            try:
                day = date.fromisoformat(row.get('day', '')).isoformat()
            except (TypeError, ValueError):
                continue
            item = {'day': day, 'event_type': row['event_type'], 'dimension': row['dimension']}
            for key in ('count', 'success_count', 'failure_count', 'duration_sum_ms'):
                if _number(row.get(key)):
                    item[key] = row[key]
            metrics.append(item)
        safe_traces = []
        for row in traces[:100]:
            if type(row) is not dict or row.get('event_type') != 'operation_completed':
                continue
            properties = row.get('properties', {})
            operation = properties.get('operation') if type(properties) is dict else None
            if operation not in _SCHEMAS['operation_completed'][1]['operation'] or not _number(row.get('duration_ms'), 7*86400000):
                continue
            item = {'operation': operation, 'duration_ms': row['duration_ms']}
            if type(row.get('success')) is bool:
                item['success'] = row['success']
            safe_traces.append(item)
        return json.dumps({'process_resources': safe_resources, 'daily_metrics': metrics,
                           'recent_operation_traces': safe_traces, 'vram': 'not_verified'}, indent=2, sort_keys=True)

    def run_checks(self):
        self.run_button.setEnabled(False)
        self.status.setText('Checking local health…')
        self._task = _HealthTask(self.service)
        self._task.signals.result.connect(self._show_report)
        self._task.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(self._task)

    def _show_report(self, report):
        self.run_button.setEnabled(True)
        self.status.setText('Health: ' + report.status)
        lines = ['Local checks completed. Untested features remain unverified.', '']
        for issue in report.issues:
            lines.extend((issue.message, 'Next step: ' + issue.action, ''))
        if not report.issues:
            lines.append('Verified local checks found no issues.')
        lines.append('Check details are available in Advanced.')
        self.health_preview.setPlainText('\n'.join(lines))
        self.advanced_preview.appendPlainText('\nLocal check details:\n' + json.dumps(report.payload(), indent=2, sort_keys=True))
        self._refresh_reviews()

    def _failed(self):
        self.run_button.setEnabled(True)
        self.status.setText('Local checks could not complete. Existing files are preserved.')

    def _refresh_reviews(self):
        self.review_choice.clear()
        try:
            for item in self.service.review_items():
                self.review_choice.addItem(item['status'] + ' · ' + item['reference_id'], item['reference_id'])
        except Exception:
            pass
        self.acknowledge_button.setEnabled(self.review_choice.count() > 0)

    def acknowledge_review(self):
        reference = self.review_choice.currentData()
        if reference is None:
            return
        try:
            self.service.acknowledge_review(reference)
        except (OSError, ValueError):
            self.status.setText('The review could not be acknowledged. Check recovery records again.')
            return
        self.status.setText('Review acknowledged. No action was replayed.')
        self._refresh_reviews()
