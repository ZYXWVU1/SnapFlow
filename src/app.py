"""Coordinate capture, background requests, and the tray lifecycle."""
import os
import logging
import sqlite3
from dataclasses import replace
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QActionGroup, QCursor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QStyle
from src.config import Config, ROOT, load_config, save_config
from src.hotkeys import Hotkeys
from src.llm_client import AnalysisError, LLMClient
from src.modes import ModeResult, ResponseFormatError, parse_result
from src.prompts import MODES
from src.screenshot import capture_screen, image_to_png_bytes, resize_if_needed
from src.ui.result_window import ResultWindow
from src.ui.selection_overlay import SelectionOverlay
from src.ui.settings_window import SettingsWindow
from src.ui.main_window import MainWindow
from src.ui.design.theme import ThemeManager
from src.smart.models import ClassificationResult, unknown
from src.smart.router import SmartRouter
from src.smart.worker import ClassificationWorker
from src.smart.presentation import detection_notice, loading_message
from src.skills.base import SkillResult
from src.skills.registry import SKILLS, SkillRegistry
from src.skills.worker import SkillExtractionWorker
from src.skills.custom.storage import CustomSkillStorage
from src.feedback.models import EditableExtraction
from src.feedback.schema import editable_schema
from src.feedback.storage import FeedbackStorage
from src.skill_versions.manager import SkillVersionManager
from src.skills.custom.worker import CustomMatchWorker
from src.workflows.storage import WorkflowStorage
from src.workflows.registry import WorkflowRegistry
from src.workflows.executor import WorkflowExecutor
from src.workflows.context import WorkflowContext
from src.workflows.history import WorkflowHistory
from src.ui.workflows.runner import WorkflowBridge, WorkflowWorker
from src.integrations.registry import IntegrationRegistry
from src.integrations.storage import ConnectionStorage
from src.integrations.credentials import CredentialService
from src.integrations.service import IntegrationService
from src.ui.integrations.connection_dialog import GoogleConnectDialog, TodoistConnectDialog
from src.ui.integrations.worker import ConnectionWorker


class WorkerSignals(QObject):
    finished = Signal(int, object, bool)


class AnalysisWorker(QRunnable):
    def __init__(self, request_id: int, client: LLMClient, data: bytes, mode: str, question: str,
                 history: list[dict[str, str]] | None = None) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.request_id, self.client, self.data = request_id, client, data
        self.mode, self.question = mode, question
        self.history = list(history or [])

    def run(self) -> None:
        try:
            if self.history:
                text = self.client.analyze_image(self.data, self.mode, self.question, history=self.history)
            else:
                text = self.client.analyze_image(self.data, self.mode, self.question)
            text = parse_result(self.mode, text)
            error = False
        except (AnalysisError, ResponseFormatError) as exc:
            text, error = str(exc), True
        except Exception:
            text, error = "An unexpected analysis error occurred. Please try again.", True
        finally:
            self.data = b""
        self.signals.finished.emit(self.request_id, text, error)


class ApplicationController(QObject):
    def __init__(self, app: QApplication, preview: bool = False) -> None:
        super().__init__()
        self.app, self.preview = app, preview
        load_dotenv(ROOT / ".env")
        warning = ""
        try:
            self.config = load_config()
        except ValueError as exc:
            self.config, warning = Config(), str(exc)
        self.mode = self.config.default_mode
        self.theme_manager = ThemeManager(app, self.config.theme)
        self.client = LLMClient()
        self.custom_storage = CustomSkillStorage()
        self.feedback_storage = None
        self.saved_example_id = None
        self.skill_registry = SkillRegistry(self.custom_storage)
        self.skill_manager = None
        self.workflow_storage = WorkflowStorage()
        self.workflow_manager = None
        self.workflow_registry = WorkflowRegistry(self.workflow_storage)
        self.workflow_history = WorkflowHistory()
        self.integration_registry = IntegrationRegistry()
        self.connection_storage = ConnectionStorage()
        self.credential_service = CredentialService()
        self.integration_service = IntegrationService(self.integration_registry,
            self.connection_storage, self.credential_service)
        self.integration_pool = QThreadPool(self)
        self.integration_pool.setMaxThreadCount(2)
        self.integration_jobs = {}
        self.integration_dialogs = []
        self.workflow_pool = QThreadPool(self)
        self.workflow_pool.setMaxThreadCount(1)
        self.workflow_jobs = {}
        self._auto_seen = set()
        self._workflow_results = []
        self.retry_skill = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.workers: dict[int, AnalysisWorker | ClassificationWorker | SkillExtractionWorker] = {}
        self.request_id = 0
        self.image_bytes = b""
        self.ask_history: list[dict[str, str]] = []
        self.last_result: ModeResult | SkillResult | None = None
        self.editable_extraction: EditableExtraction | None = None
        self.failed_question: str | None = None
        self.overlays: list[SelectionOverlay] = []
        self.capturing = False
        self.quitting = False
        self.settings: SettingsWindow | None = None
        self.main_window = MainWindow(self.config.hotkey, skills_storage=self.custom_storage,
            workflows_storage=self.workflow_storage, history=self.workflow_history, config=self.config,
            integration_registry=self.integration_registry, connection_storage=self.connection_storage)
        self.main_window.pages['evaluation'].client = self.client
        self.main_window.capture_requested.connect(self.capture)
        self.main_window.feature_requested.connect(self.open_feature)
        self.main_window.integration_connect_requested.connect(self.connect_integration)
        self.main_window.integration_test_requested.connect(self.test_integration)
        self.main_window.integration_disconnect_requested.connect(self.disconnect_integration)
        self.main_window.skill_edit_requested.connect(self.edit_skill)
        self.main_window.skill_test_requested.connect(self.test_skill)
        self.main_window.workflow_edit_requested.connect(self.edit_workflow)
        self.main_window.workflow_test_requested.connect(self.test_workflow)
        self._restore_home_after_capture = False
        self.result = ResultWindow(self.mode, self.config.always_on_top)
        self.result.set_workflow_registry(self.workflow_registry)
        self.result.workflow_requested.connect(self.run_workflow)
        self.result.workflow_cancel_requested.connect(self.cancel_workflow)
        self.result.integration_requested.connect(self.open_integrations)
        self.result.edit_requested.connect(self.edit_result)
        self.result.save_example_requested.connect(self.save_verified_example)
        self.result.ask.connect(self.analyze)
        self.result.ask_ai.connect(self.ask_about_result)
        self.result.mode_changed.connect(self.change_mode)
        self.result.closed.connect(self.discard_result)
        self.result.settings_requested.connect(self.open_settings)
        icon = QIcon(str(ROOT / "assets" / "icon.ico"))
        if icon.isNull():
            icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.workflow_bridge = WorkflowBridge(self.tray, self.ask_about_result, self)
        self.tray.setToolTip("AI Screenshot Helper")
        self.menu = QMenu()
        self.home_action = self.menu.addAction("Open Home", self.open_home)
        self.capture_action = self.menu.addAction("Capture Screenshot", self.capture)
        self.menu.addSeparator()
        mode_menu = self.menu.addMenu("Mode")
        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for value, label in MODES.items():
            action = mode_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == self.mode)
            action.triggered.connect(lambda checked=False, mode=value: self.change_mode(mode))
            self.mode_group.addAction(action)
            self.mode_actions[value] = action
        self.menu.addAction("Settings", self.open_settings)
        self.menu.addAction("Visual Skills", self.open_skills)
        self.menu.addAction("Visual Workflows", self.open_workflows)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        self.hotkeys = Hotkeys(app)
        self.hotkeys.triggered.connect(self.capture)
        try:
            self.hotkeys.register(self.config.hotkey)
        except ValueError as exc:
            warning += "\n" + str(exc)
        app.aboutToQuit.connect(self.hotkeys.close)
        if warning:
            QTimer.singleShot(0, lambda: self.show_error(warning.strip()))
        elif not os.getenv("AI_API_KEY") and not preview:
            self.tray.showMessage("AI Screenshot Helper", "Ready. Open Settings to add your API key.")

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.capture()

    def open_home(self) -> None:
        self.main_window.open_page('home')
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def open_integrations(self, integration_id=None) -> None:
        self.main_window.open_page('integrations')
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def open_feature(self, page: str) -> None:
        if page == 'skills':
            self.open_skills()
        elif page == 'workflows':
            self.open_workflows()
        elif page == 'history':
            self.open_workflows()
            self.workflow_manager.show_history()
        elif page == 'settings':
            self.open_settings()

    def connect_integration(self, integration_id: str):
        if self.quitting or integration_id in self.integration_jobs:
            return None
        if integration_id == 'google':
            connection = self.connection_storage.get('google')
            dialog = GoogleConnectDialog(self.credential_service.get('google_client_id') or '', self.main_window,
                capabilities=connection.granted_capabilities if connection else ())
            dialog.submitted.connect(lambda client_id, capabilities:
                self._start_integration_worker('google', 'connect_google', client_id, capabilities))
        elif integration_id == 'todoist':
            dialog = TodoistConnectDialog(self.main_window)
            dialog.submitted.connect(lambda token:
                self._start_integration_worker('todoist', 'connect_todoist', token))
        else:
            raise ValueError('Unknown integration.')
        dialog.finished.connect(lambda: self.integration_dialogs.remove(dialog)
            if dialog in self.integration_dialogs else None)
        self.integration_dialogs.append(dialog)
        dialog.show()
        return dialog

    def _start_integration_worker(self, integration_id, operation, *args):
        if self.quitting or integration_id in self.integration_jobs:
            return
        worker = ConnectionWorker(self.integration_service, integration_id, operation, *args)
        worker.signals.finished.connect(self.integration_finished)
        self.integration_jobs[integration_id] = worker
        self.main_window.pages['integrations'].set_busy(integration_id, True,
            'Checking…' if operation == 'test_connection' else 'Disconnecting…'
            if operation == 'disconnect' else 'Connecting…')
        self.integration_pool.start(worker)

    def test_integration(self, integration_id):
        self._start_integration_worker(integration_id, 'test_connection', integration_id)

    def disconnect_integration(self, integration_id):
        if integration_id in self.integration_jobs:
            return
        answer = QMessageBox.question(self.main_window, 'Disconnect integration',
            'Disconnect this integration? Workflows using it will need a new connection.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._start_integration_worker(integration_id, 'disconnect', integration_id)

    @Slot(str, object)
    def integration_finished(self, integration_id, outcome):
        self.main_window.pages['integrations'].refresh()
        self.main_window.pages['integrations'].set_busy(integration_id, False)
        self.integration_jobs.pop(integration_id, None)
        if not self.quitting:
            self.main_window.toast.show_message(outcome.message)

    def capture(self) -> None:
        if self.capturing or self.workers or self.quitting:
            return
        self.capturing = True
        self._restore_home_after_capture = self.main_window.isVisible()
        self.main_window.hide()
        self.result.hide()
        if self.settings:
            self.settings.hide()
        QTimer.singleShot(180, self.begin_selection)

    def begin_selection(self) -> None:
        if self.quitting:
            return
        try:
            snapshots = [(screen, capture_screen(screen)) for screen in self.app.screens()]
            if not snapshots:
                raise RuntimeError("No displays are available.")
            active = self.app.screenAt(QCursor.pos())
            for screen, image in snapshots:
                overlay = SelectionOverlay(screen, image)
                overlay.selected.connect(self.selected)
                overlay.cancelled.connect(self.cancel_selection)
                self.overlays.append(overlay)
                overlay.show()
            for overlay, (screen, _) in zip(self.overlays, snapshots):
                if screen == active:
                    overlay.activateWindow()
                    overlay.setFocus()
        except Exception:
            self.clear_overlays()
            self.show_error("Unable to capture the desktop. Try again on an unlocked display.")

    def clear_overlays(self) -> None:
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()
        self.capturing = False

    def cancel_selection(self) -> None:
        self.clear_overlays()
        if self._restore_home_after_capture:
            self.main_window.show()
            self._restore_home_after_capture = False
        if self.image_bytes:
            self.result.show()

    def selected(self, image: QImage) -> None:
        self.clear_overlays()
        self.result.set_notice()
        self.ask_history.clear()
        self.last_result = None
        self.editable_extraction = None
        self.saved_example_id = None
        self.failed_question = None
        self.result.followup.clear()
        self.retry_skill = None
        try:
            image = resize_if_needed(image, self.config.max_image_width)
            self.image_bytes = image_to_png_bytes(image)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.result.show()
        self.result.raise_()
        if self.preview:
            self.result.show_preview(image)
        else:
            self.analyze()

    def analyze(self, question: str = "") -> None:
        if not self.image_bytes or self.workers or self.preview or self.quitting:
            return
        if not question and self.failed_question is not None:
            question = self.failed_question
        self.failed_question = None
        if self.mode == 'smart' and not question.strip():
            if self.retry_skill:
                self.start_skill(*self.retry_skill)
                return
            self.run_smart_pipeline()
            return
        mode = 'ask' if self.mode == 'smart' else self.mode
        self.start_analysis(mode, question)

    def start_analysis(self, mode: str, question: str = '') -> None:
        self.request_id += 1
        worker = AnalysisWorker(self.request_id, self.client, self.image_bytes, mode, question,
                                self.ask_history if mode == 'ask' and question.strip() else None)
        worker.signals.finished.connect(self.analysis_finished)
        self.workers[self.request_id] = worker
        self.result.set_busy(loading_message(mode) if self.mode == 'smart' else 'Analyzing screenshot...')
        self.capture_action.setEnabled(False)
        self.mode_group.setEnabled(False)
        self.pool.start(worker)

    def run_smart_pipeline(self) -> None:
        self.request_id += 1
        self.last_result = None
        self.editable_extraction = None
        self.saved_example_id = None
        self.ask_history.clear()
        self.result.set_notice()
        self.result.set_busy('Understanding screenshot...')
        worker = ClassificationWorker(self.request_id, self.client, self.image_bytes)
        worker.signals.finished.connect(self.classification_finished)
        self.workers[self.request_id] = worker
        self.capture_action.setEnabled(False)
        self.mode_group.setEnabled(False)
        self.pool.start(worker)

    @Slot(int, object)
    def classification_finished(self, request_id: int, classification: ClassificationResult) -> None:
        worker = self.workers.pop(request_id, None)
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
            return
        if worker is None or request_id != self.request_id or not self.image_bytes:
            self.capture_action.setEnabled(not self.workers)
            self.mode_group.setEnabled(not self.workers)
            return
        try:
            route = SmartRouter(self.config.smart_classification_threshold).route(classification)
            if not isinstance(classification, ClassificationResult) or not classification.is_valid():
                classification = unknown('Invalid classification.')
        except Exception:
            logging.getLogger(__name__).warning('[SMART] Routing failed; falling back to Ask')
            classification, route = unknown('Routing unavailable.'), 'ask'
        self.result.set_notice(detection_notice(classification, route))
        if route in SKILLS:
            self.start_skill(SKILLS[route], classification.confidence)
        elif self.skill_registry.enabled_definitions():
            self.request_id += 1
            worker = CustomMatchWorker(self.request_id, self.client, self.image_bytes, self.skill_registry.enabled_definitions())
            worker.signals.finished.connect(self.custom_match_finished)
            self.workers[self.request_id] = worker
            self.result.set_busy('Checking custom Skills...')
            self.pool.start(worker)
        else:
            self.start_analysis(route)

    def start_skill(self, skill, confidence):
        self.request_id += 1
        self.retry_skill = (skill, confidence)
        worker = SkillExtractionWorker(self.request_id, self.client, self.image_bytes, skill, confidence)
        worker.signals.finished.connect(self.analysis_finished)
        self.workers[self.request_id] = worker
        self.result.set_busy(loading_message(skill.id) if skill.id in SKILLS else f'Matched: {skill.title}\nExtracting fields...')
        self.capture_action.setEnabled(False)
        self.mode_group.setEnabled(False)
        self.pool.start(worker)

    @Slot(int, object)
    def custom_match_finished(self, request_id, match):
        worker = self.workers.pop(request_id, None)
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
            return
        if worker is None or request_id != self.request_id or not self.image_bytes:
            self.capture_action.setEnabled(not self.workers)
            self.mode_group.setEnabled(not self.workers)
            return
        skill = self.skill_registry.get(match.skill_id) if match.skill_id else None
        if skill:
            self.result.set_notice(f'Matched: {skill.title} · Confidence: {match.confidence:.0%}')
            self.start_skill(skill, match.confidence)
        else:
            self.result.set_notice('No confident custom match. Opening Ask.')
            self.start_analysis('ask')

    @Slot(int, object, bool)
    def analysis_finished(self, request_id: int, text: ModeResult | str, error: bool) -> None:
        worker = self.workers.pop(request_id, None)
        self.capture_action.setEnabled(not self.quitting and not self.workers)
        self.mode_group.setEnabled(not self.workers)
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
        elif worker is not None and request_id == self.request_id:
            if error:
                self.failed_question = worker.question
                self.last_result = None
                self.editable_extraction = None
                self.saved_example_id = None
                if isinstance(worker, SkillExtractionWorker):
                    self.result.set_skill_error(text)
                else:
                    self.result.set_response(text, True)
            else:
                self.retry_skill = None
                self.last_result = text
                if isinstance(text, SkillResult):
                    self.prepare_editable_result(text)
                    self.saved_example_id = None
                    self.result.set_skill_result(text, editable=self.editable_extraction is not None)
                    self._auto_seen.clear()
                    self._workflow_results.clear()
                    self.dispatch_auto_workflows(text)
                    return
                conversation = None
                if text.mode == 'ask':
                    if not worker.question.strip():
                        self.ask_history.clear()
                    self.ask_history.extend([
                        {'role': 'user', 'content': worker.question.strip() or 'Explain this screenshot.'},
                        {'role': 'assistant', 'content': text.text},
                    ])
                    self.ask_history = self.ask_history[-12:]
                    conversation = text.text if len(self.ask_history) == 2 else '\n\n'.join(
                        ('You: ' if message['role'] == 'user' else 'AI: ') + message['content']
                        for message in self.ask_history)
                self.result.set_result(text, conversation)

    def prepare_editable_result(self, result: SkillResult) -> None:
        schema = editable_schema(result.skill_id, self.custom_storage)
        if schema is None:
            self.editable_extraction = None
            return
        version_id = None
        if self.custom_storage.get_skill(result.skill_id) is not None:
            try:
                versions = SkillVersionManager(self.custom_storage, self.workflow_storage)
                active = next((item for item in reversed(versions.list_versions(result.skill_id))
                               if item.status == 'published'), None)
                version_id = active.version_id if active else None
            except (OSError, sqlite3.Error):
                pass
        self.editable_extraction = EditableExtraction.create(result.skill_id, result.data, version_id)

    def edit_result(self) -> None:
        if not isinstance(self.last_result, SkillResult) or self.editable_extraction is None:
            return
        from src.ui.feedback.result_editor import ResultEditor
        schema = editable_schema(self.last_result.skill_id, self.custom_storage)
        if schema is None:
            return
        editor = ResultEditor(self.editable_extraction, schema, self.result)
        if editor.exec() != editor.DialogCode.Accepted:
            return
        if self.last_result.data != self.editable_extraction.edited_data:
            self.saved_example_id = None
        skill = self.skill_registry.get(self.last_result.skill_id)
        corrected = self.editable_extraction.edited_data.copy()
        self.last_result = replace(self.last_result, data=corrected,
                                   actions=skill.actions(corrected) if skill else self.last_result.actions)
        prior_status = self.result.workflow_status.text()
        self.result.set_skill_result(self.last_result, editable=True,
            can_verify=bool(self.editable_extraction.changed_fields) and self.saved_example_id is None)
        if prior_status:
            self.result.set_workflow_status(prior_status, running=bool(self.workflow_jobs))
        if self.editable_extraction.changed_fields and self.saved_example_id is None:
            answer = QMessageBox.question(self.result, 'Save as Verified Example?',
                'Save these corrections as a verified example for future Skill evaluation and improvement?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.save_verified_example()

    def save_verified_example(self) -> None:
        if self.editable_extraction is None or not self.editable_extraction.changed_fields or self.saved_example_id:
            return
        screenshot = None
        if self.image_bytes:
            answer = QMessageBox.question(self.result, 'Save screenshot?',
                'Include this screenshot in the verified example? It will be stored locally for image evaluation.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                screenshot = self.image_bytes
        try:
            if self.feedback_storage is None:
                self.feedback_storage = FeedbackStorage()
            record = self.feedback_storage.save_verified(self.editable_extraction, screenshot=screenshot)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self.result, 'Unable to save example', str(exc))
            return
        self.saved_example_id = record.id
        self.result.set_skill_result(self.last_result, editable=True)
        self.result.set_notice('Verified example saved locally.')

    def run_workflow(self, workflow_id: str, *, automatic=False, preview=False) -> None:
        if not isinstance(self.last_result, SkillResult) or self.quitting:
            return
        workflow = next((w for w in self.workflow_registry.matching(self.last_result.skill_id)
                         if w.id == workflow_id), None)
        if workflow is None or (automatic and not workflow.auto_run):
            return
        request_key = (self.request_id, workflow.id)
        if request_key in self.workflow_jobs or (automatic and request_key in self._auto_seen):
            return
        if automatic:
            self._auto_seen.add(request_key)
        context = WorkflowContext.from_result(self.last_result, request_id=str(self.request_id))
        worker = WorkflowWorker(workflow, context, WorkflowExecutor(self.skill_registry), self.workflow_bridge,
                                preview=preview, storage=self.workflow_storage,
                                integration_service=self.integration_service)
        worker.signals.finished.connect(self.workflow_finished)
        self.workflow_jobs[request_key] = worker
        for button in self.result.workflow_buttons:
            button.setEnabled(False)
        self.result.set_workflow_status('Running ' + workflow.name + '…', running=True)
        self.workflow_pool.start(worker)

    def dispatch_auto_workflows(self, result: SkillResult) -> None:
        if self.quitting or not isinstance(result, SkillResult):
            return
        workflows = [w for w in self.workflow_registry.matching(result.skill_id) if w.auto_run]
        for workflow in workflows:
            self.run_workflow(workflow.id, automatic=True)
        if len(workflows) > 1:
            self.result.set_workflow_status(f'{len(workflows)} Auto Workflows queued in order.', running=True)

    def cancel_workflow(self) -> None:
        for worker in self.workflow_jobs.values():
            worker.cancel.set()

    @Slot(object, object)
    def workflow_finished(self, worker, execution) -> None:
        self.workflow_jobs.pop((int(worker.context.request_id), worker.definition.id), None)
        if execution is not None and not worker.preview:
            try:
                self.workflow_history.record(worker.definition, execution, worker.context.skill_id)
            except OSError:
                logging.getLogger(__name__).warning('Unable to write Workflow history.')
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
            return
        if int(worker.context.request_id) != self.request_id or not isinstance(self.last_result, SkillResult):
            return
        running = bool(self.workflow_jobs)
        for button in self.result.workflow_buttons:
            button.setEnabled(not running)
        if execution is None:
            self._workflow_results.append(worker.definition.name + ': failed unexpectedly')
            self.result.set_workflow_status('\n'.join(self._workflow_results), running=running)
            return
        lines = [f'{worker.definition.name}: {execution.status}']
        if execution.error:
            lines.append(execution.error)
        lines.extend(f'{step.action_id}: {step.status}' +
                     (f' · {step.error}' if step.error else f' · {step.message}' if step.message else '')
                     for step in execution.steps)
        self._workflow_results.append('\n'.join(lines))
        self.result.set_workflow_status('\n\n'.join(self._workflow_results), running=running)
        self.main_window.pages['integrations'].refresh()
        for step in execution.steps:
            if step.status == 'success' and step.action_id in (
                'google_calendar_create_event', 'google_sheets_append_row', 'todoist_create_task'):
                self.main_window.toast.show_message(step.message)
        if worker.definition.auto_run and execution.status in ('failed', 'partial_success'):
            self.tray.showMessage('Workflow failed', worker.definition.name + ': ' + execution.status)

    def ask_about_result(self, question: str = '') -> None:
        if self.workers or not self.image_bytes:
            return
        if self.last_result:
            self.ask_history = [
                {'role': 'user', 'content': 'Analyze this screenshot.'},
                {'role': 'assistant', 'content': self.last_result.text},
            ]
        self.change_mode('ask', analyze=False)
        self.analyze(question or 'Explain the extracted information in this screenshot.')

    def discard_result(self) -> None:
        self.retry_skill = None
        self.result.set_notice()
        self.image_bytes = b""
        self.ask_history.clear()
        self.last_result = None
        self.editable_extraction = None
        self.saved_example_id = None
        self.failed_question = None
        self.result.followup.clear()
        self.request_id += 1

    def change_mode(self, mode: str, analyze: bool = True) -> None:
        if self.workers:
            return
        self.result.set_notice()
        self.retry_skill = None
        self.failed_question = None
        self.mode = mode
        self.mode_actions[mode].setChecked(True)
        self.result.mode.blockSignals(True)
        self.result.mode.setCurrentIndex(self.result.mode.findData(mode))
        self.result.mode.blockSignals(False)
        if analyze and self.image_bytes and not self.workers:
            self.analyze()

    def open_skills(self) -> None:
        self._ensure_skill_manager()
        self.skill_manager.refresh()
        self.skill_manager.show()
        self.skill_manager.raise_()
        self.skill_manager.activateWindow()

    def _ensure_skill_manager(self):
        from src.ui.skills.skill_manager import SkillManager
        if self.skill_manager is None:
            self.skill_manager = SkillManager(self.custom_storage, self.client, self.result,
                workflows=self.workflow_storage, feedback_storage=self.feedback_storage)
            self.skill_manager.finished.connect(lambda _: self.main_window.pages['skills'].refresh())
        return self.skill_manager

    def edit_skill(self, skill_id):
        skill = self.custom_storage.get_skill(skill_id)
        if skill is None:
            self.main_window.pages['skills'].refresh()
            return None
        editor = self._ensure_skill_manager().show_editor(skill)
        editor.saved.connect(lambda _: QTimer.singleShot(0, self.main_window.pages['skills'].refresh))
        return editor

    def test_skill(self, skill_id):
        skill = self.custom_storage.get_skill(skill_id)
        if skill is None:
            self.main_window.pages['skills'].refresh()
            return None
        from src.ui.skills.skill_test_dialog import SkillTestDialog
        dialog = SkillTestDialog(skill, self.client, self.result)
        self._ensure_skill_manager().dialogs.append(dialog)
        dialog.show()
        return dialog

    def open_workflows(self) -> None:
        self._ensure_workflow_manager()
        self.workflow_manager.refresh()
        self.workflow_manager.show()
        self.workflow_manager.raise_()
        self.workflow_manager.activateWindow()

    def _ensure_workflow_manager(self):
        from src.ui.workflows.workflow_manager import WorkflowManager
        if self.workflow_manager is None:
            self.workflow_manager = WorkflowManager(self.workflow_storage, self.skill_registry, self.result,
                allow_auto=True, result_provider=lambda: self.last_result if isinstance(self.last_result, SkillResult) else None,
                history=self.workflow_history, connection_storage=self.connection_storage,
                open_integrations=self.open_integrations, integration_service=self.integration_service)
            self.workflow_manager.finished.connect(lambda _: self.main_window.pages['workflows'].refresh())
        return self.workflow_manager

    def edit_workflow(self, workflow_id):
        definition = self.workflow_storage.get_workflow(workflow_id)
        if definition is None:
            self.main_window.pages['workflows'].refresh()
            return None
        editor = self._ensure_workflow_manager().show_editor(definition)
        editor.saved.connect(lambda _: QTimer.singleShot(0, self.main_window.pages['workflows'].refresh))
        return editor

    def test_workflow(self, workflow_id):
        definition = self.workflow_storage.get_workflow(workflow_id)
        if definition is None:
            self.main_window.pages['workflows'].refresh()
            return
        if not isinstance(self.last_result, SkillResult) or self.last_result.skill_id != definition.trigger.skill_id:
            self.main_window.toast.show_message('Capture a screenshot matching this Visual Skill, then run the test.')
            return
        manager = self._ensure_workflow_manager()
        manager.refresh()
        for index in range(manager.list.count()):
            item = manager.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == workflow_id:
                manager.list.setCurrentItem(item)
                manager.test_selected()
                return

    def open_settings(self) -> None:
        if self.settings and self.settings.isVisible():
            self.settings.showNormal()
            self.settings.raise_()
            self.settings.activateWindow()
            return
        if self.settings:
            self.settings.deleteLater()
        self.settings = SettingsWindow(self.config, self.result)
        self.settings.submitted.connect(self.apply_settings)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def apply_settings(self, config: Config, key: str) -> None:
        previous = self.config
        try:
            self.hotkeys.register(config.hotkey)
            try:
                save_config(config)
            except OSError:
                self.hotkeys.register(previous.hotkey)
                raise
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.settings, "Unable to save settings", str(exc))
            return
        self.config = config
        self.main_window.config = config
        self.main_window.pages['settings'].refresh(config)
        self.theme_manager.set_preference(config.theme)
        self.main_window.hotkey_label.setText(' + '.join(part.capitalize() for part in config.hotkey.split('+')))
        if key:
            os.environ["AI_API_KEY"] = key
        visible = self.result.isVisible()
        self.result.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, config.always_on_top)
        if visible:
            self.result.show()
        self.settings.key.clear()
        self.settings.accept()
        self.change_mode(config.default_mode)

    def show_error(self, message: str) -> None:
        self.result.set_response(message, error=True)
        self.result.show()

    def quit(self) -> None:
        self.quitting = True
        self.hotkeys.close()
        self.clear_overlays()
        self.main_window.close()
        self.image_bytes = b""
        if self.settings:
            self.settings.close()
        if self.skill_manager:
            self.skill_manager.close()
        if self.workflow_manager:
            self.workflow_manager.close()
        if self.workers:
            self.result.set_response("Finishing the current request before quitting...", error=True)
            self.result.show()
            self.capture_action.setEnabled(False)
        else:
            self.finish_quit()

    def finish_quit(self) -> None:
        if QThreadPool.globalInstance().activeThreadCount() or self.workflow_jobs or self.integration_jobs:
            self.result.set_response('Finishing the current skill request before quitting...', error=True)
            self.result.show()
            QTimer.singleShot(50, self.finish_quit)
            return
        self.pool.waitForDone()
        self.workflow_pool.waitForDone()
        self.integration_pool.waitForDone()
        self.tray.hide()
        self.app.quit()
