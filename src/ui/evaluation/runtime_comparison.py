"""Explicit, background local/cloud comparison on one frozen evaluation set."""
from dataclasses import replace
import os
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QScrollArea, QWidget,
)

from src.config import DEFAULT_AI_BASE_URL, DEFAULT_AI_MODEL
from src.network_policy import get_network_policy
from src.ai.routing_context import hardware_identity
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


def _label(text=''):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class ComparisonSignals(QObject):
    finished = Signal(str, object, str)


class ComparisonTaskWorker(QRunnable):
    def __init__(self, task, operation):
        super().__init__()
        self.task, self.operation = task, operation
        self.signals = ComparisonSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.task, self.operation(), '')
        except Exception as exc:
            self.signals.finished.emit(self.task, None, str(exc) or type(exc).__name__)


class ModelComparisonDialog(QDialog):
    completed = Signal(object)
    running_changed = Signal(bool)

    def __init__(self, datasets, versions, feedback, reports, config, parent=None, *,
                 client=None, usage_storage=None, max_cases=100, initial_dataset_id=None,
                 service_factory=None, cancelled=None):
        super().__init__(parent)
        self.datasets, self.versions, self.feedback, self.reports = datasets, versions, feedback, reports
        self.config, self.client = config, client
        self.usage_storage = usage_storage
        self.max_cases = max(1, int(max_cases))
        self.initial_dataset_id = initial_dataset_id
        self.service_factory = service_factory
        self._external_cancelled = cancelled
        self._cancel_event = threading.Event()
        self._close_when_done = False
        self.policy = getattr(client, 'policy', None) or get_network_policy()
        self.loading = True
        self.worker = None
        self._tasks = {}
        self._choices = {}
        self._cost_estimate = None
        self._cost_key = None
        self.setWindowTitle('SnapFlow - Compare AI Models')
        self.resize(760, 760)
        self.setMinimumSize(560, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Compare AI Models', 'Measure a local and cloud model on the same Skill version and dataset.'))
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        body.setSpacing(SPACING['md'])
        root.addWidget(self.content_scroll, 1)
        selection = Card()
        form = QFormLayout()
        self.dataset = QComboBox()
        self.version = QComboBox()
        self.case_limit = QSpinBox()
        self.case_limit.setRange(1, self.max_cases)
        self.local_model = QLineEdit(config.local_ai_model or '')
        self.local_model.setPlaceholderText('Local server model ID')
        self.local_version = QLineEdit(getattr(config, 'local_ai_model_version', None) or '')
        self.local_version.setPlaceholderText('Immutable model checksum or exact release version (required)')
        self.cloud_model = QLineEdit(config.ai_model or os.getenv('AI_MODEL') or DEFAULT_AI_MODEL)
        self.cloud_version = QLineEdit(getattr(config, 'ai_model_version', None) or '')
        self.cloud_version.setPlaceholderText('Exact provider model snapshot/version (required)')
        self.hardware_profile_id = QLineEdit()
        self.hardware_profile_id.setPlaceholderText('Detected before a confirmed run if blank')
        self.hardware_profile_id.setReadOnly(True)
        hardware_row = QHBoxLayout()
        hardware_row.addWidget(self.hardware_profile_id, 1)
        self.hardware_button = AppButton('Inspect Hardware')
        self.hardware_button.clicked.connect(self.inspect_hardware)
        hardware_row.addWidget(self.hardware_button)
        form.addRow('Dataset', self.dataset)
        form.addRow('Skill version', self.version)
        form.addRow('Cases per model', self.case_limit)
        form.addRow('LOCAL model ID', self.local_model)
        form.addRow('LOCAL model version', self.local_version)
        form.addRow('CLOUD model ID', self.cloud_model)
        form.addRow('CLOUD model version', self.cloud_version)
        form.addRow('Local hardware identity', hardware_row)
        selection.content.addLayout(form)
        body.addWidget(selection)
        self.cost_preview = _label('Loading local evaluation metadata. No AI requests have been sent.')
        body.addWidget(self.cost_preview)
        self.preview_button = AppButton('Refresh Cloud Cost Estimate')
        self.preview_button.clicked.connect(self.refresh_cost_estimate)
        body.addWidget(self.preview_button)
        self.result_details = _label('Results will show separate quality, reliability, latency, API cost, and privacy dimensions. Local latency applies to this detected hardware only.')
        body.addWidget(self.result_details)
        self.metrics = QTableWidget(9, 3)
        self.metrics.setHorizontalHeaderLabels(('Dimension', 'LOCAL', 'CLOUD'))
        self.metrics.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.metrics.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.metrics.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.metrics.verticalHeader().hide()
        self.metrics.setMinimumHeight(300)
        dimensions = ('Field accuracy', 'Schema validity', 'Required field coverage',
            'Missing field accuracy', 'Hallucination rate', 'Classification accuracy',
            'Failure rate', 'Average latency', 'API cost per request')
        for row, title in enumerate(dimensions):
            self.metrics.setItem(row, 0, QTableWidgetItem(title))
            self.metrics.setItem(row, 1, QTableWidgetItem('Unavailable'))
            self.metrics.setItem(row, 2, QTableWidgetItem('Unavailable'))
        body.addWidget(self.metrics)
        body.addStretch(1)
        self.status = _label('Loading datasets and versions…')
        root.addWidget(self.status)
        row = QHBoxLayout()
        self.close_button = AppButton('Close')
        self.close_button.clicked.connect(self.reject)
        self.cancel_button = AppButton('Cancel Comparison')
        self.cancel_button.clicked.connect(self.cancel_work)
        self.cancel_button.setEnabled(False)
        self.run_button = AppButton('Run Comparison', variant='primary')
        self.run_button.clicked.connect(self.run_comparison)
        self.run_button.setEnabled(False)
        row.addWidget(self.close_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        row.addWidget(self.run_button)
        root.addLayout(row)
        for label in self.findChildren(QLabel):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.dataset.currentIndexChanged.connect(self._dataset_changed)
        self.case_limit.valueChanged.connect(self._update_request_preview)
        self.cloud_model.textChanged.connect(self._update_request_preview)
        self.local_model.textChanged.connect(self._update_request_preview)
        self._dispatch('metadata', self._load_choices)

    def _dispatch(self, task, operation):
        worker = ComparisonTaskWorker(task, operation)
        self._tasks[task] = worker
        worker.signals.finished.connect(self._task_finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)
        return worker

    def _load_choices(self):
        return [(entry, self.versions.list_versions(entry.skill_id),
                 len(self.datasets.available_examples(entry))) for entry in self.datasets.list_datasets()]

    @Slot(str, object, str)
    def _task_finished(self, task, result, error):
        self._tasks.pop(task, None)
        if task == 'metadata':
            self.loading = False
            if error:
                self.status.setText('Could not load comparison metadata: ' + error)
                return
            self._choices = {entry.id: (entry, versions, count) for entry, versions, count in result}
            self.dataset.clear()
            for entry, _versions, count in result:
                self.dataset.addItem(f'{entry.name} · {entry.dataset_type} · {count} available cases', entry.id)
            if self.initial_dataset_id:
                index = self.dataset.findData(self.initial_dataset_id)
                if index >= 0:
                    self.dataset.setCurrentIndex(index)
            self._dataset_changed()
            self.run_button.setEnabled(bool(result))
            self.status.setText('Ready. No comparison runs automatically.' if result else 'Create a dataset of verified examples before comparing models.')
        elif task == 'hardware':
            self.hardware_button.setEnabled(True)
            self.status.setText('Hardware inspection failed: ' + error if error else 'Hardware identity recorded. Actual acceleration depends on the runtime.')
            if not error:
                self.hardware_profile_id.setText(result)
        elif task == 'cost':
            self.preview_button.setEnabled(True)
            if error:
                self.status.setText('Cost estimate unavailable: ' + error)
                return
            key, estimate = result
            if key == self._preview_key():
                self._cost_key, self._cost_estimate = key, estimate
                self._update_request_preview()
        elif task == 'comparison':
            self.worker = None
            self._set_running(False)
            if self._is_cancelled():
                self.status.setText('Comparison cancelled. No incomplete comparison profile was published.')
            elif error:
                self.status.setText('Comparison failed: ' + error)
            else:
                self._show_result(result)
                self.completed.emit(result)
        if self._close_when_done and not self._tasks:
            super().reject()

    def _dataset_changed(self, *_args):
        choice = self._choices.get(self.dataset.currentData())
        self.version.clear()
        if choice:
            _entry, versions, count = choice
            for version in versions:
                self.version.addItem(f'{version.version_label} · {version.status}', version.version_id)
            self.case_limit.setMaximum(max(1, min(count, self.max_cases)))
            self.case_limit.setValue(self.case_limit.maximum())
            self.case_limit.setEnabled(count > 0)
        self._update_request_preview()

    def _preview_key(self):
        return (self.dataset.currentData(), self.case_limit.value(), self.cloud_model.text().strip(),
                self.config.ai_base_url or os.getenv('AI_BASE_URL') or DEFAULT_AI_BASE_URL)

    def _update_request_preview(self, *_args):
        choice = self._choices.get(self.dataset.currentData())
        count = min(self.case_limit.value(), choice[2]) if choice else 0
        estimate = self._cost_estimate if self._cost_key == self._preview_key() else None
        text = f'{count} case(s) per model · up to {count * 2} LOCAL requests and {count * 2} potential CLOUD requests. '
        if estimate is not None and estimate.estimated_cost_usd is not None:
            text += f'Estimated cloud API cost: about ${estimate.estimated_cost_usd:.4f}, based on {estimate.sample_count} recent evaluation request(s). Actual charges may differ.'
        else:
            text += 'Cloud API cost estimate unavailable until matching provider/model usage is measured; charges may apply. Local API Cost: $0.'
        text += ' Saved evaluation screenshots and prompts are sent to the cloud model only after confirmation.'
        self.cost_preview.setText(text)

    def refresh_cost_estimate(self):
        if self.worker is not None:
            return
        if self.usage_storage is None:
            self.status.setText('Cloud cost estimate unavailable: no usage ledger is configured.')
            return
        if 'cost' in self._tasks:
            return
        key = self._preview_key()
        self.preview_button.setEnabled(False)
        self._dispatch('cost', lambda: (key, self.usage_storage.evaluation_preview(key[1], key[3], key[2], comparisons=1)))

    def inspect_hardware(self):
        if self.worker is not None or 'hardware' in self._tasks:
            return
        def operation():
            from src.ai.hardware import HardwareProfileService
            return hardware_identity(HardwareProfileService().detect())
        self.hardware_button.setEnabled(False)
        self._dispatch('hardware', operation)

    def _selection(self):
        choice = self._choices.get(self.dataset.currentData())
        if choice is None or not self.version.currentData() or not choice[2]:
            raise ValueError('Choose an available dataset and a Skill version for the same Skill.')
        local_model, cloud_model = self.local_model.text().strip(), self.cloud_model.text().strip()
        local_version, cloud_version = self.local_version.text().strip(), self.cloud_version.text().strip()
        if not local_model or not cloud_model:
            raise ValueError('Enter both local and cloud model IDs.')
        if not local_version or not cloud_version:
            raise ValueError('Enter exact local and cloud model versions before collecting comparison evidence.')
        if any(len(value) > 128 for value in (local_model, cloud_model)) or any(len(value) > 512 for value in (local_version, cloud_version)):
            raise ValueError('Model IDs or versions are too long.')
        if self.config.private_mode or self.policy.private_mode:
            raise ValueError('Private Mode blocks cloud model comparisons. Disable Private Mode to run this comparison.')
        return dict(version_id=self.version.currentData(), dataset_id=self.dataset.currentData(),
            local_model_id=local_model, cloud_model_id=cloud_model,
            local_model_version=local_version, cloud_model_version=cloud_version,
            hardware_profile_id=self.hardware_profile_id.text().strip() or None,
            max_cases=min(self.case_limit.value(), choice[2]))

    def run_comparison(self):
        if self.worker is not None or self.loading:
            return
        if self._external_cancelled and self._external_cancelled():
            self.status.setText('AI evaluation cancelled.')
            return
        self._cancel_event.clear()
        try:
            selection = self._selection()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._update_request_preview()
        message = (f'Compare LOCAL {selection["local_model_id"]} ({selection["local_model_version"]}) '
            f'and CLOUD {selection["cloud_model_id"]} ({selection["cloud_model_version"]}) '
            f'on the same selected Skill version and dataset?\n\n{self.cost_preview.text()}')
        box = QMessageBox(QMessageBox.Icon.Question, 'Confirm Model Comparison', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._set_running(True)
        self.status.setText('Comparing the two pinned runtimes in the background…')
        self.worker = self._dispatch('comparison', lambda: self._compare(selection))

    def _set_running(self, running):
        for field in (self.dataset, self.version, self.case_limit, self.local_model, self.local_version,
                      self.cloud_model, self.cloud_version, self.hardware_button, self.preview_button,
                      self.run_button):
            field.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.close_button.setText('Cancel & Close' if running else 'Close')
        self.running_changed.emit(running)

    def _service(self):
        if self.service_factory is not None:
            return self.service_factory()
        from src.ai.evaluation import ModelComparisonService
        from src.ai.performance import ModelPerformanceStore
        store = getattr(self.client, 'performance_store', None) or ModelPerformanceStore(self.datasets.path)
        return ModelComparisonService(self.feedback, self.datasets, self.versions, self.reports, store, self.policy)

    def _compare(self, selection):
        if self._is_cancelled():
            raise ValueError('AI evaluation cancelled.')
        from src.ai.hardware import HardwareProfileService
        from src.llm_client import LLMClient
        local_config = replace(self.config, ai_execution_mode='local_only', allow_cloud_fallback=False,
                               local_ai_model=selection['local_model_id'],
                               local_ai_model_version=selection['local_model_version'])
        cloud_config = replace(self.config, ai_execution_mode='cloud_only', allow_cloud_fallback=False,
                               ai_model=selection['cloud_model_id'],
                               ai_model_version=selection['cloud_model_version'])
        callback = getattr(self.client, 'usage_callback', None)
        paths = getattr(self.client, 'paths', None)
        local = LLMClient(usage_callback=callback, config=local_config, policy=self.policy, paths=paths)
        cloud = LLMClient(usage_callback=callback, config=cloud_config, policy=self.policy, paths=paths)
        local.managed_runtime = getattr(self.client, 'managed_runtime', None)
        if not selection['hardware_profile_id']:
            selection = {**selection, 'hardware_profile_id': hardware_identity(HardwareProfileService().detect())}
        return self._service().compare(local.scoped(self._is_cancelled), cloud.scoped(self._is_cancelled),
                                       **selection, confirmed=True, cancelled=self._is_cancelled)

    def _is_cancelled(self):
        return self._cancel_event.is_set() or bool(self._external_cancelled and self._external_cancelled())

    def cancel_work(self):
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        if self.worker is not None:
            self.status.setText('Cancelling comparison. No further AI requests will be started.')

    def _show_result(self, result):
        local, cloud = result.local_profile, result.cloud_profile
        self.result_details.setText(
            f'LOCAL: {local.runtime_id} / {local.model_id} · version {local.model_version} · n={local.case_count}\n'
            f'CLOUD: {cloud.runtime_id} / {cloud.model_id} · version {cloud.model_version} · n={cloud.case_count}\n'
            f'Skill version: {local.skill_version_id} · Dataset revision: {local.dataset_revision}\n'
            f'Local hardware: {local.hardware_profile_id or "Unavailable"}')
        keys = ('field_accuracy', 'schema_validity', 'required_field_coverage', 'missing_field_accuracy',
            'hallucination_rate', 'classification_accuracy', 'failure_rate', 'avg_latency_ms',
            'estimated_cost_per_request')
        for column, profile in ((1, local), (2, cloud)):
            for row, key in enumerate(keys):
                value = getattr(profile, key, None)
                if row == 8 and profile.local:
                    label = 'API Cost: $0'
                elif value is None:
                    label = 'Unavailable'
                elif row == 7:
                    label = f'{value:.0f} ms'
                elif row == 8:
                    label = f'Estimated ${value:.6f}'
                else:
                    label = f'{value:.1%}'
                self.metrics.setItem(row, column, QTableWidgetItem(label))
        self.status.setText('Comparison completed. Separate measured dimensions are saved with model, Skill, dataset, and hardware identity. Small samples do not establish general model quality.')

    def reject(self):
        if self._tasks:
            self._close_when_done = True
            self.cancel_work()
            self.hide()
            return
        super().reject()

    def closeEvent(self, event):
        if self._tasks:
            self._close_when_done = True
            self.cancel_work()
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)
