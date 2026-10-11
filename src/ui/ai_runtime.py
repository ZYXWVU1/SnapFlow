"""Native AI runtime preferences and explicit background diagnostics.

Constructing this dialog performs no hardware probing, file copying, or network
requests. All expensive operations are opt-in and dispatched to Qt's pool.
Closing cancels self-tests and imports. Managed startup retains its application
owner and may still wait for its bounded 60-second startup timeout.
"""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from src.paths import AppPaths
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


EXECUTION_MODES = (
    ('automatic', 'Automatic'), ('prefer_local', 'Prefer Local'),
    ('local_only', 'Local Only'), ('cloud_only', 'Cloud Only'),
    ('ask_every_time', 'Ask Every Time'),
)


def _label(text='', *, muted=False):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if muted:
        label.setProperty('role', 'muted')
    return label


def _size_text(size):
    value = float(max(0, int(size)))
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}'
        value /= 1024


class RuntimeOperationSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    finished = Signal(str)


class RuntimeOperationWorker(QRunnable):
    """Run one bounded operation; widgets are only touched by queued slots."""
    def __init__(self, operation, callback):
        super().__init__()
        self.operation = operation
        self.callback = callback
        self.signals = RuntimeOperationSignals()

    def run(self):
        try:
            self.signals.succeeded.emit(self.operation, self.callback())
        except Exception as exc:
            self.signals.failed.emit(self.operation, str(exc) or type(exc).__name__)
        finally:
            self.signals.finished.emit(self.operation)


class AIRuntimeDialog(QDialog):
    saved = Signal(object)
    managed_runtime_changed = Signal(object)

    def __init__(self, config, paths=None, parent=None, *, save_handler=None):
        super().__init__(parent)
        self.config = config
        self.save_handler = save_handler
        self.paths = paths or AppPaths()
        self._operations = {}
        self._closing_cancel = threading.Event()
        self._close_when_done = False
        self._operation_buttons = []
        self._managed_runtime = None
        self._models = {}
        self.setWindowTitle('SnapFlow - AI & Models')
        self.resize(710, 720)
        self.setMinimumSize(480, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('AI & Models', 'Choose where AI runs and inspect your local runtime.'))
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        container = QWidget()
        self.content_scroll.setWidget(container)
        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        body.setSpacing(SPACING['lg'])
        root.addWidget(self.content_scroll, 1)

        execution = Card()
        execution.content.addWidget(_label('AI Execution'))
        form = QFormLayout()
        self.execution_mode = QComboBox()
        for value, title in EXECUTION_MODES:
            self.execution_mode.addItem(title, value)
        self.execution_mode.setCurrentIndex(self.execution_mode.findData(
            getattr(config, 'ai_execution_mode', 'cloud_only')))
        self.execution_mode.setAccessibleName('AI execution mode')
        form.addRow('Execution mode', self.execution_mode)
        self.private_mode = QCheckBox('Private Mode — block external network access')
        self.private_mode.setChecked(getattr(config, 'private_mode', False))
        form.addRow(self.private_mode)
        self.allow_cloud_fallback = QCheckBox('Allow cloud fallback if local inference fails')
        self.allow_cloud_fallback.setChecked(getattr(config, 'allow_cloud_fallback', False))
        form.addRow(self.allow_cloud_fallback)
        execution.content.addLayout(form)
        self.privacy_status = _label()
        self.privacy_status.setAccessibleName('Current privacy policy')
        execution.content.addWidget(self.privacy_status)
        execution.content.addWidget(_label('Changes take effect when saved. Private Mode takes precedence over the execution mode.', muted=True))
        body.addWidget(execution)

        local = Card()
        local.content.addWidget(_label('Local OpenAI-compatible server'))
        local_form = QFormLayout()
        self.endpoint = QLineEdit(getattr(config, 'local_ai_endpoint', 'http://127.0.0.1:8080/v1'))
        self.endpoint.setAccessibleName('Local AI endpoint')
        self.model = QLineEdit(getattr(config, 'local_ai_model', None) or '')
        self.model.setPlaceholderText('Model ID reported by the local server')
        self.model.setAccessibleName('Local AI model')
        self.model_version = QLineEdit(getattr(config, 'local_ai_model_version', None) or '')
        self.model_version.setPlaceholderText('Exact release revision or model checksum')
        self.model_version.setAccessibleName('Local AI model version')
        self.vision = QCheckBox('Vision / image input')
        self.vision.setChecked(getattr(config, 'local_ai_vision', False))
        self.structured_output = QCheckBox('Structured JSON output')
        self.structured_output.setChecked(getattr(config, 'local_ai_structured_output', False))
        self.context_length = QSpinBox()
        self.context_length.setRange(256, 1000000)
        self.context_length.setValue(getattr(config, 'local_ai_context_length', 8192))
        self.context_length.setAccessibleName('Local model context length')
        local_form.addRow('Endpoint', self.endpoint)
        local_form.addRow('Model ID', self.model)
        local_form.addRow('Model version', self.model_version)
        local_form.addRow('Declared capabilities', self.vision)
        local_form.addRow('', self.structured_output)
        local_form.addRow('Context length (tokens)', self.context_length)
        local.content.addLayout(local_form)
        local.content.addWidget(_label('Use loopback (localhost, 127.0.0.1, or ::1). Capability settings must match your model; loading it does not verify Vision support.', muted=True))
        self.runtime_status = _label('Local runtime not checked.')
        local.content.addWidget(self.runtime_status)
        test_row = QHBoxLayout()
        self.test_runtime_button = self._button('Check Runtime', self.check_runtime)
        self.list_server_models_button = self._button('List Server Models', self.list_server_models)
        self.test_text_button = self._button('Test Text', lambda: self.test_model('text'))
        self.test_vision_button = self._button('Test Vision', lambda: self.test_model('vision'))
        self.test_json_button = self._button('Test JSON', lambda: self.test_model('json'))
        for button in (self.test_runtime_button, self.list_server_models_button, self.test_text_button,
                       self.test_vision_button, self.test_json_button):
            test_row.addWidget(button)
        local.content.addLayout(test_row)
        local.content.addWidget(_label('Self-tests use synthetic text and a generated color image. No captured screenshot or saved Memory record is used.', muted=True))
        body.addWidget(local)

        embeddings = Card()
        embeddings.content.addWidget(_label('Local Embeddings'))
        self.semantic_search_enabled = QCheckBox('Enable local semantic search')
        self.semantic_search_enabled.setChecked(config.semantic_search_enabled)
        embeddings.content.addWidget(self.semantic_search_enabled)
        embedding_form = QFormLayout()
        self.embedding_model = QLineEdit(getattr(config, 'local_embedding_model', None) or '')
        self.embedding_model.setAccessibleName('Local embedding model')
        self.embedding_version = QLineEdit(getattr(config, 'local_embedding_version', '1'))
        self.embedding_version.setPlaceholderText('Exact embedding revision')
        self.embedding_version.setAccessibleName('Local embedding model revision')
        self.embedding_dimension = QSpinBox()
        self.embedding_dimension.setRange(0, 65536)
        self.embedding_dimension.setValue(getattr(config, 'local_embedding_dimension', 0))
        self.embedding_dimension.setSpecialValueText('Not configured')
        embedding_form.addRow('Embedding model ID', self.embedding_model)
        embedding_form.addRow('Embedding revision', self.embedding_version)
        embedding_form.addRow('Embedding dimension', self.embedding_dimension)
        embeddings.content.addLayout(embedding_form)
        embeddings.content.addWidget(_label('Use the actual embedding dimension. Local semantic search needs a configured embedding model and dimension. Changing the model, revision, or dimension requires Rebuild Memory Search Index in Settings.', muted=True))
        body.addWidget(embeddings)

        models = Card()
        models.content.addWidget(_label('Local Models'))
        models.content.addWidget(_label('Imported files are copied into your SnapFlow user data folder. No model download or installation occurs automatically.', muted=True))
        import_form = QFormLayout()
        self.import_name = QLineEdit()
        self.import_name.setPlaceholderText('Optional name for the imported model')
        self.projector_path = QLineEdit()
        self.projector_path.setPlaceholderText('Optional multimodal projector GGUF')
        projector_row = QWidget()
        projector_layout = QHBoxLayout(projector_row)
        projector_layout.setContentsMargins(0, 0, 0, 0)
        projector_layout.addWidget(self.projector_path, 1)
        self.projector_button = self._button('Choose Projector', self.choose_projector)
        projector_layout.addWidget(self.projector_button)
        import_form.addRow('Display name', self.import_name)
        import_form.addRow('Vision projector', projector_row)
        models.content.addLayout(import_form)
        self.model_list = QListWidget()
        self.model_list.setAccessibleName('Imported local models')
        self.model_list.setMinimumHeight(110)
        self.model_list.currentItemChanged.connect(self._model_selection_changed)
        models.content.addWidget(self.model_list)
        self.model_details = _label('Refresh to inspect installed models. Select a model to view its declared capabilities.')
        models.content.addWidget(self.model_details)
        model_row = QHBoxLayout()
        self.refresh_models_button = self._button('Refresh Models', self.refresh_models)
        self.import_model_button = self._button('Import GGUF', self.import_model)
        self.use_model_button = self._button('Use Model', self.use_selected_model)
        self.delete_model_button = self._button('Delete Model', self.delete_selected_model, variant='danger')
        for button in (self.refresh_models_button, self.import_model_button,
                       self.use_model_button, self.delete_model_button):
            model_row.addWidget(button)
        models.content.addLayout(model_row)
        body.addWidget(models)

        managed = Card()
        managed.content.addWidget(_label('Managed llama.cpp Runtime (optional)'))
        executable_row = QWidget()
        executable_layout = QHBoxLayout(executable_row)
        executable_layout.setContentsMargins(0, 0, 0, 0)
        self.executable = QLineEdit()
        self.executable.setPlaceholderText('Choose an existing llama-server executable')
        self.executable.setAccessibleName('llama-server executable')
        executable_layout.addWidget(self.executable, 1)
        self.executable_button = self._button('Choose Executable', self.choose_executable)
        executable_layout.addWidget(self.executable_button)
        managed.content.addWidget(executable_row)
        self.managed_status = _label('Stopped. Select an imported model and a runtime executable to start.')
        managed.content.addWidget(self.managed_status)
        managed_row = QHBoxLayout()
        self.start_runtime_button = self._button('Start Selected Model', self.start_managed_runtime)
        self.stop_runtime_button = self._button('Stop Runtime', self.stop_managed_runtime)
        managed_row.addWidget(self.start_runtime_button)
        managed_row.addWidget(self.stop_runtime_button)
        managed.content.addLayout(managed_row)
        body.addWidget(managed)

        hardware = Card()
        hardware.content.addWidget(_label('AI Hardware'))
        self.hardware_status = _label('Hardware not inspected. Detected devices do not guarantee runtime acceleration.')
        hardware.content.addWidget(self.hardware_status)
        self.inspect_hardware_button = self._button('Inspect Hardware', self.inspect_hardware)
        hardware.content.addWidget(self.inspect_hardware_button)
        body.addWidget(hardware)

        cloud = Card()
        cloud.content.addWidget(_label('Cloud Provider'))
        cloud.content.addWidget(_label(f'Configured endpoint: {config.ai_base_url or "Default / environment"}\nModel: {config.ai_model or "Default / environment"}'))
        cloud_form = QFormLayout()
        self.cloud_version = QLineEdit(getattr(config, 'ai_model_version', None) or '')
        self.cloud_version.setPlaceholderText('Exact provider model snapshot or release version')
        self.cloud_version.setAccessibleName('Cloud AI model version')
        cloud_form.addRow('Cloud model version', self.cloud_version)
        cloud.content.addLayout(cloud_form)
        cloud.content.addWidget(_label('Exact model versions identify comparison evidence. An unversioned model cannot reuse benchmark measurements safely.', muted=True))
        cloud.content.addWidget(_label('Edit the cloud provider and secure API key in Edit Settings. API keys are not stored in this dialog.', muted=True))
        body.addWidget(cloud)
        body.addStretch(1)
        self.operation_status = _label()
        root.addWidget(self.operation_status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.private_mode.toggled.connect(self._refresh_privacy)
        self.execution_mode.currentIndexChanged.connect(self._refresh_privacy)
        self.allow_cloud_fallback.toggled.connect(self._refresh_privacy)
        self._refresh_privacy()
        for label in self.findChildren(QLabel):
            label.setTextFormat(Qt.TextFormat.PlainText)

    def _button(self, text, action, *, variant='secondary'):
        button = AppButton(text, variant=variant)
        button.clicked.connect(lambda _checked=False: action())
        self._operation_buttons.append(button)
        return button

    def _refresh_privacy(self, *_args):
        private = self.private_mode.isChecked()
        self.allow_cloud_fallback.setEnabled(not private)
        if private:
            text = 'Private Mode active: external network access is blocked. AI requests must stay on this computer; cloud fallback is blocked.'
        elif self.execution_mode.currentData() == 'local_only':
            text = 'Local Only: AI requests stay on the local runtime. Private Mode is off, so other authorized integrations can still use the network.'
        elif self.execution_mode.currentData() == 'cloud_only':
            text = 'Cloud Only: AI requests may send selected content to your configured cloud provider. Private Mode is off.'
        elif self.execution_mode.currentData() == 'ask_every_time':
            text = 'Ask Every Time: each AI request requires a runtime choice. Private Mode is off.'
        else:
            text = 'Hybrid routing: local and cloud runtimes may be selected according to the request. Private Mode is off.'
            if self.allow_cloud_fallback.isChecked():
                text += ' Cloud fallback is allowed.'
            else:
                text += ' Cloud fallback is blocked.'
        self.privacy_status.setText(text)

    def current_config(self):
        return replace(self.config,
            ai_execution_mode=self.execution_mode.currentData(),
            private_mode=self.private_mode.isChecked(),
            allow_cloud_fallback=self.allow_cloud_fallback.isChecked() and not self.private_mode.isChecked(),
            local_ai_endpoint=self.endpoint.text().strip(),
            local_ai_model=self.model.text().strip() or None,
            local_ai_model_version=self.model_version.text().strip() or None,
            ai_model_version=self.cloud_version.text().strip() or None,
            local_ai_vision=self.vision.isChecked(),
            local_ai_structured_output=self.structured_output.isChecked(),
            local_ai_context_length=self.context_length.value(),
            semantic_search_enabled=self.semantic_search_enabled.isChecked(),
            local_embedding_model=self.embedding_model.text().strip() or None,
            local_embedding_version=self.embedding_version.text().strip(),
            local_embedding_dimension=self.embedding_dimension.value())

    def submit(self):
        try:
            config = self.current_config()
        except (ValueError, TypeError) as exc:
            self.operation_status.setText(f'Invalid settings: {exc}')
            return
        if self.save_handler is not None and self.save_handler(config) is False:
            self.operation_status.setText('Preferences could not be saved. The current privacy policy remains active.')
            return
        self.config = config
        self.saved.emit(config)
        self.accept()

    def _run_operation(self, name, operation, callback=None):
        if self._operations or self._closing_cancel.is_set():
            return
        worker = RuntimeOperationWorker(name, operation)
        self._operations[name] = (worker, callback)
        worker.signals.succeeded.connect(self._operation_succeeded, Qt.ConnectionType.QueuedConnection)
        worker.signals.failed.connect(self._operation_failed, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(self._operation_finished, Qt.ConnectionType.QueuedConnection)
        for button in self._operation_buttons:
            button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.operation_status.setText(f'{name}…')
        QThreadPool.globalInstance().start(worker)

    @Slot(str, object)
    def _operation_succeeded(self, name, result):
        entry = self._operations.get(name)
        # Process startup cannot be abandoned. Its completion must still refresh
        # application ownership and credentials while the dialog is hidden.
        if self._closing_cancel.is_set() and name != 'Start Runtime':
            return
        self.operation_status.setText(f'{name} completed.')
        if entry and entry[1]:
            try:
                entry[1](result)
            except Exception as exc:
                self.operation_status.setText(f'{name}: {exc}')

    @Slot(str, str)
    def _operation_failed(self, name, message):
        self.operation_status.setText(f'{name}: {message}')

    @Slot(str)
    def _operation_finished(self, name):
        self._operations.pop(name, None)
        if self._close_when_done and not self._operations:
            super().reject()
            return
        for button in self._operation_buttons:
            button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def _local_runtime(self, config):
        from src.ai.local import LocalOpenAICompatibleRuntime
        from src.ai.models import ModelCapabilities
        credentials = {}
        if self._managed_runtime is not None and self._managed_runtime.endpoint == config.local_ai_endpoint.rstrip('/'):
            credentials['api_key'] = self._managed_runtime.api_key
            credentials['manager'] = self._managed_runtime
        return LocalOpenAICompatibleRuntime(config.local_ai_endpoint,
            model_id=config.local_ai_model,
            capabilities=ModelCapabilities(text=True, vision=config.local_ai_vision,
                structured_output=config.local_ai_structured_output,
                max_context_length=config.local_ai_context_length), **credentials)

    def _validated_operation_config(self):
        try:
            return self.current_config()
        except (ValueError, TypeError) as exc:
            self.operation_status.setText(f'Invalid settings: {exc}')
            return None

    def check_runtime(self):
        config = self._validated_operation_config()
        if config:
            self._run_operation('Check Runtime', lambda: self._local_runtime(config).health_check(),
                lambda healthy: self.runtime_status.setText('Local runtime is healthy.' if healthy else 'Local runtime is unavailable.'))

    def list_server_models(self):
        config = self._validated_operation_config()
        if config:
            self._run_operation('List Server Models', lambda: self._local_runtime(config).list_models(), self._server_models_ready)

    def _server_models_ready(self, models):
        names = [str(item.get('id', 'Unknown')) if isinstance(item, dict)
                 else str(getattr(item, 'id', item)) for item in models]
        self.runtime_status.setText('Server models: ' + (', '.join(names) or 'none reported'))

    def test_model(self, kind):
        config = self._validated_operation_config()
        if config is None:
            return
        if not config.local_ai_model:
            self.operation_status.setText('Choose a local model ID before testing.')
            return
        if kind == 'vision' and not config.local_ai_vision:
            self.operation_status.setText('Enable the declared Vision capability before running its test.')
            return
        if kind == 'json' and not config.local_ai_structured_output:
            self.operation_status.setText('Enable the declared structured JSON capability before running its test.')
            return
        imported = self._models.get(config.local_ai_model)
        capability = {'json': 'structured_output'}.get(kind, kind)
        record_model_id = imported.id if imported and getattr(imported.capabilities, capability, False) else None
        self._run_operation(f'Test {kind.title()}', lambda: self._model_test(config, kind, record_model_id), self.runtime_status.setText)

    def _model_test(self, config, kind, record_model_id=None):
        start = time.monotonic()
        success = False
        try:
            result = self._perform_model_test(config, kind)
            success = True
            return result
        finally:
            if record_model_id is not None:
                self._model_manager().record_self_test(record_model_id,
                    {'json': 'structured_output'}.get(kind, kind), success=success,
                    output_validated=success, latency_ms=(time.monotonic() - start) * 1000,
                    runtime_id='local_openai')

    def _perform_model_test(self, config, kind):
        from src.ai.models import AIRequest
        self._check_closing()
        runtime = self._local_runtime(config)
        start = time.monotonic()
        if kind == 'vision':
            image = QImage(64, 64, QImage.Format.Format_RGB888)
            image.fill(QColor(0, 0, 255))
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, 'PNG'):
                raise ValueError('Could not create the synthetic Vision test image.')
            image_bytes = bytes(buffer.data())
            buffer.close()
            response = runtime.analyze_image(AIRequest(prompt='What color is this image? Reply only with the color name.', image_bytes=image_bytes, task_type='vision',
                                                      cancelled=self._closing_cancel.is_set))
            content = response.content or ''
            if content.strip().rstrip('.').lower() != 'blue':
                raise ValueError('Vision output validation failed: expected the generated blue image to be described as blue.')
        elif kind == 'json':
            response = runtime.generate_text(AIRequest(prompt='Return a JSON object with exactly one property: "ok": true.',
                task_type='structured_extraction', cancelled=self._closing_cancel.is_set,
                response_schema={'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}))
            result = response.structured_data
            if result is None:
                result = json.loads(response.content or '')
            if not isinstance(result, dict) or set(result) != {'ok'} or result['ok'] is not True:
                raise ValueError('Structured JSON output validation failed: expected {"ok": true}.')
        else:
            response = runtime.generate_text(AIRequest(prompt='Reply with one short greeting.', task_type='text',
                                                       cancelled=self._closing_cancel.is_set))
            if not (response.content or '').strip():
                raise ValueError('Text output validation failed: model returned no text.')
        elapsed = (time.monotonic() - start) * 1000
        self._check_closing()
        return f'{kind.title()} test passed · LOCAL · {response.runtime_id} / {response.model_id} · {elapsed:.0f} ms · output validation passed · API Cost: $0'

    def _model_manager(self):
        from src.ai.local_models import LocalModelManager
        def loaded_ids():
            if self._managed_runtime is None:
                return ()
            state = self._managed_runtime.state
            return (state.model_id,) if state.model_id and state.state in ('starting', 'loading_model', 'ready', 'busy') else ()
        return LocalModelManager(self.paths, loaded_model_ids=loaded_ids)

    def refresh_models(self):
        self._run_operation('Refresh Models', lambda: self._model_manager().list_models(), self._models_ready)

    def _models_ready(self, models):
        current = self.model_list.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.model_list.clear()
        self._models = {model.id: model for model in models}
        for model in models:
            item = QListWidgetItem(f'{model.display_name} · {model.backend} · {model.status}')
            item.setData(Qt.ItemDataRole.UserRole, model.id)
            self.model_list.addItem(item)
            if model.id == selected:
                self.model_list.setCurrentItem(item)
        if not models:
            self.model_details.setText('No local models installed. Import an existing GGUF to get started.')

    def _selected_model(self):
        item = self.model_list.currentItem()
        return self._models.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _model_selection_changed(self, *_args):
        model = self._selected_model()
        if model is None:
            self.model_details.setText('Select an imported model.')
            return
        capability = model.capabilities
        capabilities = [name for name in ('text', 'vision', 'embeddings', 'structured_output', 'tool_calling') if getattr(capability, name, False)]
        verified = ', '.join(model.verified_capabilities) or 'none recorded (refresh after a test)'
        self.model_details.setText(f'Name: {model.display_name}\nBackend: {model.backend} · Format: {model.format}\nCapabilities: {", ".join(capabilities) or "none declared"}\nValidated self-tests: {verified}\nQuantization: {model.quantization or "Unavailable"} · Disk: {_size_text(model.size_bytes)}\nStatus: {model.status}\nHardware fit: not benchmarked. Use a self-test to verify compatibility.')

    def choose_projector(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose Vision Projector', '', 'GGUF model (*.gguf)')
        if filename:
            self.projector_path.setText(filename)

    def choose_executable(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose llama-server Executable', '', 'Executable (*.exe);;All files (*)')
        if filename:
            self.executable.setText(filename)

    def import_model(self):
        config = self._validated_operation_config()
        if config is None:
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Import Existing Model', '', 'GGUF model (*.gguf)')
        if not filename:
            return
        name = self.import_name.text().strip() or Path(filename).stem
        projector = self.projector_path.text().strip() or None
        def operation():
            from src.ai.models import ModelCapabilities
            manager = self._model_manager()
            manager.import_model(filename, display_name=name, backend='llama.cpp', projector_path=projector,
                cancel=self._closing_cancel,
                capabilities=ModelCapabilities(text=True, vision=config.local_ai_vision,
                    structured_output=config.local_ai_structured_output,
                    max_context_length=config.local_ai_context_length))
            self._check_closing()
            return manager.list_models()
        self._run_operation('Import Model', operation, self._models_ready)

    def use_selected_model(self):
        model = self._selected_model()
        if model is None:
            self.operation_status.setText('Select an imported model first.')
            return
        self._apply_model(model)
        self.operation_status.setText('Model selected. Save to apply, then verify the server reports this model ID.')

    def _apply_model(self, model):
        self.model.setText(model.id)
        self.model_version.setText(model.checksum or '')
        self.vision.setChecked(model.capabilities.vision)
        self.structured_output.setChecked(model.capabilities.structured_output)
        if model.capabilities.max_context_length:
            self.context_length.setValue(model.capabilities.max_context_length)

    def delete_selected_model(self):
        model = self._selected_model()
        if model is None:
            self.operation_status.setText('Select an imported model first.')
            return
        self._run_operation('Inspect Model Deletion', lambda: self._model_manager().delete_preview(model.id),
                            lambda preview: self._confirm_delete(model.id, model.display_name, preview))

    def _confirm_delete(self, model_id, name, preview):
        size = getattr(preview, 'size_bytes', None)
        if size is None and isinstance(preview, dict):
            size = preview.get('size_bytes', preview.get('reclaim_bytes', 0))
        message = f'Delete {name}?\nDisk space to reclaim: {_size_text(size or 0)}\nOnly files owned by this imported model will be deleted. A loaded model must be stopped first.'
        box = QMessageBox(QMessageBox.Icon.Question, 'Delete Local Model', message,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        # The preview worker's completion is still queued. Dispatch after its
        # finished slot, rather than re-entering a currently active operation.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._delete_confirmed(model_id))

    def _delete_confirmed(self, model_id):
        def operation():
            if self._managed_runtime is not None:
                state = self._managed_runtime.state
                if getattr(state, 'model_id', None) == model_id:
                    self._managed_runtime.stop()
            manager = self._model_manager()
            manager.delete_model(model_id, confirmed=True)
            return manager.list_models()
        self._run_operation('Delete Model', operation, self._models_ready)

    def start_managed_runtime(self):
        if self._operations or self._closing_cancel.is_set():
            return
        model = self._selected_model()
        executable = self.executable.text().strip()
        if model is None or not executable:
            self.operation_status.setText('Select an imported model and an existing llama-server executable first.')
            return
        from src.ai.process import ManagedLlamaRuntimeManager
        if self._managed_runtime is None:
            # Constructor performs no file/network work. Register ownership
            # before dispatch so application shutdown can always stop it.
            self._managed_runtime = ManagedLlamaRuntimeManager(self.paths, executable=executable)
            self.managed_runtime_changed.emit(self._managed_runtime)
        manager = self._managed_runtime
        def operation():
            manager.stop()
            manager.executable = Path(executable)
            # Keep startup bounded and retain its finish callback on close.
            return manager.start(model, timeout_seconds=60)
        self._run_operation('Start Runtime', operation, self._managed_started)

    def _managed_started(self, state):
        self.managed_runtime_changed.emit(self._managed_runtime)
        self.managed_status.setText(f'State: {state.state}\nModel: {state.model_id or "Unavailable"} · Process: {state.pid or "Unavailable"} · Port: {state.port or "Unavailable"}')
        if state.port and state.state == 'ready':
            self.endpoint.setText(f'http://127.0.0.1:{state.port}/v1')
            self.model.setText(state.model_id or '')
            model = self._models.get(state.model_id)
            if model is not None:
                self._apply_model(model)
            self.operation_status.setText('Managed runtime is ready. Save the assigned endpoint and model to use it.')

    def stop_managed_runtime(self):
        if self._managed_runtime is None:
            self.managed_status.setText('No runtime has been started by this dialog.')
            return
        self._run_operation('Stop Runtime', self._managed_runtime.stop,
                            lambda _state: self.managed_status.setText('Stopped.'))

    @property
    def managed_runtime(self):
        return self._managed_runtime

    def set_managed_runtime(self, manager):
        """Reuse the application-owned runtime across settings dialog lifetimes."""
        self._managed_runtime = manager
        if manager is not None:
            state = manager.state
            self.managed_status.setText(f'State: {state.state}\nModel: {state.model_id or "Unavailable"} · Process: {state.pid or "Unavailable"} · Port: {state.port or "Unavailable"}')

    def inspect_hardware(self):
        def operation():
            from src.ai.hardware import HardwareProfileService
            return HardwareProfileService().detect()
        self._run_operation('Inspect Hardware', operation, self._hardware_ready)

    def _hardware_ready(self, profile):
        gpu = []
        for device in profile.gpu_devices:
            vram = getattr(device, 'total_vram_mb', None)
            gpu.append(f'{device.name} · VRAM: {vram} MB' if vram is not None else f'{device.name} · VRAM: Unavailable')
        lines = [f'OS: {profile.os_version}',
            f'CPU: {profile.cpu_name or "Unavailable"} · {profile.logical_cpu_count} logical processors',
            'RAM: ' + (f'{profile.available_ram_mb} MB available' if profile.available_ram_mb is not None else 'Unavailable') +
                ' / ' + (f'{profile.total_ram_mb} MB total' if profile.total_ram_mb is not None else 'Unavailable'),
            'GPU: ' + ('; '.join(gpu) or 'Unavailable'),
            'NPU: ' + ('; '.join(str(getattr(device, 'name', device)) for device in profile.npu_devices) or 'Unavailable'),
            'Available execution providers: ' + (', '.join(profile.available_execution_providers) or 'Unavailable'),
            'Windows ML: ' + ('Available' if profile.windows_ml_available else 'Unavailable'),
            'Hardware detection is incomplete on some systems. Actual acceleration must be confirmed by your runtime.']
        self.hardware_status.setText('\n'.join(lines))

    def _check_closing(self):
        if self._closing_cancel.is_set():
            raise ValueError('AI runtime operation cancelled.')

    def _cancel_on_close(self):
        self._closing_cancel.set()
        if self._operations:
            self._close_when_done = True
            self.operation_status.setText('Cancelling pending self-tests and imports. Managed startup may take up to 60 seconds to finish.')
            self.hide()
            return True
        return False

    def reject(self):
        if self._cancel_on_close():
            return
        super().reject()

    def closeEvent(self, event):
        if self._cancel_on_close():
            event.ignore()
            return
        super().closeEvent(event)
