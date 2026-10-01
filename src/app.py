"""Coordinate capture, background requests, and the tray lifecycle."""
import os
import logging
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QActionGroup, QCursor, QDesktopServices, QIcon, QImage
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon, QStyle
from src.config import Config, load_config, save_config
from src.paths import AppPaths
from src.memory.storage import DuplicateMemoryError, MemoryStore
from src.memory.recall import MemoryRecallService
from src.context.models import ContextRequest, ContextSession
from src.context.permissions import ContextPermissionService
from src.context.controller import ContextController
from src.suggestions.service import ActionSuggestionService
from src.skill_actions import execute_action
from src.app_version import APP_NAME, APP_VERSION
from src.backup import BackupError, BackupService
from src.diagnostics import DiagnosticsService, configure_logging, classify_error
from src.usage import UsageStorage
from src.hotkeys import Hotkeys
from src.llm_client import AnalysisError, LLMClient
from src.modes import ModeResult, ResponseFormatError, parse_result
from src.prompts import MODES
from src.screenshot import capture_screen, image_to_png_bytes, resize_if_needed
from src.ui.result_window import ResultWindow
from src.ui.memory_dialogs import MemorySaveDialog, MemoryDetailDialog
from src.ui.context_assistant import ContextAssistantDialog
from src.ui.selection_overlay import SelectionOverlay
from src.ui.settings_window import SettingsWindow
from src.ui.onboarding import OnboardingDialog
from src.ui.onboarding_worker import OnboardingConnectionWorker
from src.ui.diagnostics_worker import BasicHealthCheckWorker
from src.ui.main_window import MainWindow
from src.ui.update_dialog import UpdateDialog, official_release_page
from src.ui.update_worker import UpdateCheckWorker
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
from src.integrations.credentials import CredentialError, CredentialService
from src.integrations.google.config import (
    resolve_google_client_id,
    resolve_google_client_secret,
)
from src.integrations.service import IntegrationService
from src.ui.integrations.connection_dialog import GoogleConnectDialog, TodoistConnectDialog
from src.ui.integrations.worker import CalendarEventWorker, ConnectionWorker


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
        except Exception as exc:
            report = classify_error(exc)
            logging.getLogger(__name__).exception(
                "Unhandled analysis error [%s] category=%s", report.reference_id, report.category)
            text, error = f"{report.user_message} Reference: {report.reference_id}", True
        finally:
            self.data = b""
        self.signals.finished.emit(self.request_id, text, error)


class MemoryRecallSignals(QObject):
    finished = Signal(object, str)


class MemoryRecallWorker(QRunnable):
    def __init__(self, store, client, question, filters):
        super().__init__()
        self.signals = MemoryRecallSignals()
        self.store, self.client = store, client
        self.question, self.filters = question, filters

    def run(self):
        try:
            answer = MemoryRecallService(self.store, self.client).ask(
                self.question, enabled=True, filters=self.filters)
            error = ''
        except (AnalysisError, ValueError, sqlite3.Error) as exc:
            answer, error = None, str(exc)
        except Exception:
            logging.getLogger(__name__).error('Memory question failed.')
            answer, error = None, 'Unable to answer this Memory question.'
        self.signals.finished.emit(answer, error)


class ContextSignals(QObject):
    finished = Signal(str, object, str)


class ContextWorker(QRunnable):
    def __init__(self, controller, session, request):
        super().__init__()
        self.signals = ContextSignals()
        self.controller, self.session, self.request = controller, session, request

    def run(self):
        try:
            result = self.controller.answer(self.session, self.request)
            error = ''
        except AnalysisError:
            result, error = None, ('Contextual AI is unavailable. '
                'Your saved memories remain searchable locally.')
        except (ValueError, OSError, sqlite3.Error) as exc:
            result, error = None, str(exc)
        except Exception:
            logging.getLogger(__name__).error('Contextual answer failed.')
            result, error = None, 'Unable to answer with the selected context.'
        self.signals.finished.emit(self.request.request_id, result, error)


class ApplicationController(QObject):
    api_usage_changed = Signal(object)
    monthly_cost_warning = Signal(float)

    def __init__(self, app: QApplication, preview: bool = False, paths=None, *, suppress_onboarding=False) -> None:
        super().__init__()
        self.app, self.preview = app, preview
        self.paths = paths or AppPaths()
        configure_logging(self.paths)
        self.diagnostics_service = DiagnosticsService(self.paths)
        self.backup_service = BackupService(self.paths)
        self.usage_storage = UsageStorage(self.paths.learning_database)
        self.memory_store = MemoryStore(self.paths)
        self.memory_pool = QThreadPool(self)
        self.memory_pool.setMaxThreadCount(1)
        self.memory_recall_worker = None
        self.context_permissions = ContextPermissionService()
        self.context_pool = QThreadPool(self)
        self.context_pool.setMaxThreadCount(1)
        self.context_workers = {}
        self.context_session = None
        self.context_dialog = None
        self.context_latest_request = None
        self.context_suggestion_service = ActionSuggestionService(
            self.memory_store, self.context_permissions)
        self.context_suggestions = {}
        load_dotenv(self.paths.environment_file)
        self._environment_api_key = os.getenv("AI_API_KEY", "")
        self.api_key_source = "environment" if self._environment_api_key else ""
        self.api_key_stored = False
        self.credential_service = CredentialService()
        try:
            stored_api_key = self.credential_service.get_ai_api_key()
        except CredentialError as exc:
            logging.getLogger(__name__).warning(
                "Unable to read the saved AI key from Windows Credential Manager: %s", exc)
        else:
            self.api_key_stored = bool(stored_api_key)
            if not self._environment_api_key and stored_api_key:
                os.environ["AI_API_KEY"] = stored_api_key
                self.api_key_source = "credential"
        warning = ""
        try:
            self.config = load_config(self.paths.config_file)
        except ValueError as exc:
            self.config, warning = Config(), str(exc)
        if self.config.ai_base_url:
            os.environ['AI_BASE_URL'] = self.config.ai_base_url
        if self.config.ai_model:
            os.environ['AI_MODEL'] = self.config.ai_model
        self.usage_session_started = datetime.now(timezone.utc).isoformat()
        self.mode = self.config.default_mode
        self.theme_manager = ThemeManager(app, self.config.theme)
        self.client = LLMClient(usage_callback=self.record_api_usage)
        self.custom_storage = CustomSkillStorage(path=self.paths.custom_skills_file)
        self.feedback_storage = None
        self.saved_example_id = None
        self.skill_registry = SkillRegistry(self.custom_storage)
        self.skill_manager = None
        self.workflow_storage = WorkflowStorage(path=self.paths.workflows_file)
        self.workflow_manager = None
        self.workflow_registry = WorkflowRegistry(self.workflow_storage)
        self.workflow_history = WorkflowHistory(path=self.paths.workflow_history_file)
        self.integration_registry = IntegrationRegistry()
        self.connection_storage = ConnectionStorage(path=self.paths.integrations_file)
        self.integration_service = IntegrationService(self.integration_registry,
            self.connection_storage, self.credential_service,
            google_client_id=resolve_google_client_id(self.paths.resource_root),
            google_client_secret=resolve_google_client_secret(self.paths.resource_root))
        self.integration_pool = QThreadPool(self)
        self.integration_pool.setMaxThreadCount(2)
        self.integration_jobs = {}
        self.calendar_event_worker = None
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
        self.capture_created_at = None
        self.ask_history: list[dict[str, str]] = []
        self.last_result: ModeResult | SkillResult | None = None
        self.editable_extraction: EditableExtraction | None = None
        self.failed_question: str | None = None
        self.overlays: list[SelectionOverlay] = []
        self.capturing = False
        self.quitting = False
        self.settings: SettingsWindow | None = None
        self.update_worker: UpdateCheckWorker | None = None
        self.health_check_worker: BasicHealthCheckWorker | None = None
        self.onboarding_dialog: OnboardingDialog | None = None
        self.onboarding_connection_worker: OnboardingConnectionWorker | None = None
        self._onboarding_capture_test = False
        self.main_window = MainWindow(self.config.hotkey, skills_storage=self.custom_storage,
            workflows_storage=self.workflow_storage, history=self.workflow_history, config=self.config,
            integration_registry=self.integration_registry, connection_storage=self.connection_storage,
            paths=self.paths, memory_store=self.memory_store)
        self.main_window.pages['evaluation'].client = self.client
        self.main_window.pages['evaluation'].usage_storage = self.usage_storage
        self.main_window.pages['evaluation'].max_evaluation_cases = self.config.max_evaluation_cases
        self.main_window.capture_requested.connect(self.capture)
        self.main_window.feature_requested.connect(self.open_feature)
        self.main_window.integration_connect_requested.connect(self.connect_integration)
        self.main_window.integration_test_requested.connect(self.test_integration)
        self.main_window.integration_disconnect_requested.connect(self.disconnect_integration)
        self.main_window.skill_edit_requested.connect(self.edit_skill)
        self.main_window.skill_test_requested.connect(self.test_skill)
        self.main_window.workflow_edit_requested.connect(self.edit_workflow)
        self.main_window.workflow_test_requested.connect(self.test_workflow)
        self.main_window.backup_export_requested.connect(self.export_backup)
        self.main_window.backup_restore_requested.connect(self.restore_backup)
        self.main_window.open_data_folder_requested.connect(self.open_data_folder)
        self.main_window.storage_usage_requested.connect(self.refresh_storage_usage)
        self.main_window.support_bundle_requested.connect(self.export_support_bundle)
        self.main_window.health_check_requested.connect(self.run_health_check)
        self.main_window.usage_summary_requested.connect(self.refresh_usage_summary)
        self.main_window.update_check_requested.connect(self.check_for_updates)
        self.main_window.onboarding_requested.connect(self.show_onboarding)
        self.main_window.memory_open_requested.connect(self.open_memory_record)
        self.main_window.memory_ask_requested.connect(self.ask_memory)
        self.main_window.memory_rebuild_requested.connect(self.rebuild_memory_index)
        self.main_window.pages['memory'].set_ai_enabled(
            self.config.visual_memory_enabled and self.config.ai_memory_questions)
        self.api_usage_changed.connect(self.update_usage_summary)
        self.monthly_cost_warning.connect(self.show_monthly_cost_warning)
        self.update_usage_summary(self.usage_storage.monthly_summary(
            session_start=self.usage_session_started))
        self._restore_home_after_capture = False
        self.result = ResultWindow(self.mode, self.config.always_on_top)
        self.result.memory_save_enabled = (
            self.config.visual_memory_enabled and self.config.show_memory_save_button)
        self.result.context_enabled = self.config.contextual_assistant_enabled
        self.result.set_workflow_registry(self.workflow_registry)
        self.result.workflow_requested.connect(self.run_workflow)
        self.result.workflow_cancel_requested.connect(self.cancel_workflow)
        self.result.integration_requested.connect(self.open_integrations)
        self.result.calendar_event_requested.connect(self.create_calendar_event)
        self.result.edit_requested.connect(self.edit_result)
        self.result.save_example_requested.connect(self.save_verified_example)
        self.result.save_to_memory_requested.connect(self.save_current_to_memory)
        self.result.context_requested.connect(self.open_context_assistant)
        self.result.ask.connect(self.analyze)
        self.result.ask_ai.connect(self.ask_about_result)
        self.result.mode_changed.connect(self.change_mode)
        self.result.closed.connect(self.discard_result)
        self.result.settings_requested.connect(self.open_settings)
        self._refresh_google_calendar_action()
        icon = QIcon(str(self.paths.resource_path("assets/icon.ico")))
        if icon.isNull():
            icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.workflow_bridge = WorkflowBridge(self.tray, self.ask_about_result, self)
        self.tray.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.home_action = self.menu.addAction("Open Home", self.open_home)
        self.menu.addAction("Visual Memory", self.open_memory)
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
        if self.config.automatic_update_checks:
            QTimer.singleShot(1500, lambda: self.check_for_updates(manual=False))
        self.hotkeys = Hotkeys(app)
        self.hotkeys.triggered.connect(self.capture)
        self.hotkey_error = ''
        try:
            self.hotkeys.register(self.config.hotkey)
        except ValueError as exc:
            self.hotkey_error = str(exc)
            warning += "\n" + self.hotkey_error
        app.aboutToQuit.connect(self.hotkeys.close)
        if warning:
            error_context = 'hotkey' if self.hotkey_error else 'configuration'
            QTimer.singleShot(0, lambda: self.report_error(ValueError(warning.strip()), context=error_context))
        elif not os.getenv("AI_API_KEY") and not preview:
            self.tray.showMessage(APP_NAME, "Ready. Open Settings to add your API key.")
        if (not suppress_onboarding and not preview and not os.getenv("AI_API_KEY")
                and not self.config.onboarding_complete):
            QTimer.singleShot(0, self.show_onboarding)

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.capture()

    def open_home(self) -> None:
        self.main_window.open_page('home')
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def open_memory(self) -> None:
        self.main_window.open_page('memory')
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def open_memory_record(self, memory_id: str) -> None:
        try:
            dialog = MemoryDetailDialog(self.memory_store, memory_id, self.main_window)
        except ValueError as exc:
            QMessageBox.warning(self.main_window, 'Visual Memory', str(exc))
            return
        dialog.exec()
        if dialog.changed:
            self.main_window.pages['memory'].refresh()

    def open_context_assistant(self) -> None:
        if (self.quitting or not self.config.contextual_assistant_enabled or not self.image_bytes or
                not isinstance(self.last_result, (SkillResult, ModeResult))):
            return
        if self.context_dialog is not None:
            self.context_dialog.show()
            self.context_dialog.raise_()
            return
        self.context_session = ContextSession.new(self.capture_created_at or uuid.uuid4().hex)
        dialog = ContextAssistantDialog(self.memory_store, self.result,
            memory_enabled=self.config.visual_memory_enabled,
            memory_search_enabled=self.config.context_memory_search_enabled)
        dialog.question_requested.connect(self.submit_context_question)
        dialog.scope_changed.connect(self.context_scope_changed)
        dialog.permission_revoked.connect(self.revoke_context_permission)
        dialog.clear_requested.connect(self.clear_context_session)
        dialog.memory_open_requested.connect(self.open_memory_record)
        dialog.source_removed.connect(self.remove_context_source)
        dialog.current_source_requested.connect(self.result.raise_)
        dialog.suggestion_requested.connect(self.execute_context_suggestion)
        dialog.cancel_requested.connect(self.cancel_context_request)
        dialog.closed.connect(self.close_context_session)
        self.context_dialog = dialog
        dialog.show()

    def _current_context_data(self):
        result = self.last_result
        if isinstance(result, SkillResult):
            return {'skill': result.skill_id, 'title': result.title,
                    'data': result.data, 'warnings': result.warnings}
        if isinstance(result, ModeResult):
            return {'mode': result.mode, 'text': result.text, 'data': result.data}
        return None

    def submit_context_question(self, question: str, scope: str, memory_ids) -> None:
        session, dialog = self.context_session, self.context_dialog
        if self.quitting or session is None or dialog is None or not self.image_bytes:
            return
        try:
            if scope == 'current_only':
                if session.scope != scope:
                    self.context_permissions.revoke_permission(session)
            elif not self.config.visual_memory_enabled:
                raise ValueError('Visual Memory access is disabled in Settings.')
            elif scope == 'selected_memories':
                ids = tuple(memory_ids)
                if session.scope != scope or session.selected_memory_ids != ids:
                    self.context_permissions.grant_selected(session, ids)
            elif scope == 'authorized_memory_search':
                if not self.config.context_memory_search_enabled:
                    raise ValueError('Enable context Memory search in Settings first.')
                if session.scope != scope:
                    self.context_permissions.grant_search(session)
            else:
                raise ValueError('Unknown context scope.')
            request = ContextRequest.new(session, question,
                current_skill_result=self._current_context_data())
            self.context_permissions.validate_request(session, request)
        except ValueError as exc:
            dialog.show_error(str(exc))
            return
        worker = ContextWorker(ContextController(self.memory_store, self.client,
            self.context_permissions), session, request)
        worker.signals.finished.connect(self.context_finished)
        self.context_workers[request.request_id] = worker
        self.context_latest_request = request.request_id
        self.context_suggestions.clear()
        dialog.show_suggestions(())
        dialog.set_busy(question)
        self.context_pool.start(worker)

    @Slot(str, object, str)
    def context_finished(self, request_id, result, error):
        worker = self.context_workers.pop(request_id, None)
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
            return
        if (worker is None or self.context_dialog is None or
                self.context_session is not worker.session or
                request_id != self.context_latest_request or
                worker.request.permission_version != worker.session.permission_version):
            return
        if error:
            self.context_dialog.show_error(error)
        else:
            self.context_dialog.show_result(result)
            suggestions = self.context_suggestion_service.suggest(
                self.context_session, worker.request, self.last_result,
                result, self.workflow_registry)
            self.context_suggestions = {item.suggestion_id: item for item in suggestions}
            self.context_dialog.show_suggestions(suggestions)

    def execute_context_suggestion(self, suggestion_id):
        suggestion = self.context_suggestions.get(suggestion_id)
        if (suggestion is None or self.context_dialog is None or
                self.context_session is None or
                suggestion.request_id != self.context_latest_request):
            return
        try:
            self.context_suggestion_service.validate(suggestion,
                self.context_session, self.last_result, self.workflow_registry)
        except ValueError as exc:
            self.context_dialog.show_error(str(exc))
            self.context_suggestions.pop(suggestion_id, None)
            return
        if suggestion.action_id == 'workflow_preview':
            self.run_workflow(suggestion.workflow_id, preview=True)
            self.context_dialog.show_error('Workflow preview started. Review it in the screenshot result.')
            return
        outcome = execute_action(suggestion.action_id, self.last_result)
        if not outcome.success or outcome.kind != 'copy':
            self.context_dialog.show_error(outcome.message or 'This suggestion is unavailable.')
            return
        QApplication.clipboard().setText(outcome.payload)
        self.context_dialog.show_error('Copied current result details to clipboard.')

    def revoke_context_permission(self):
        if self.context_session is not None and self.context_session.scope != 'current_only':
            self.context_permissions.revoke_permission(self.context_session)
            self.context_latest_request = None
            self.context_suggestions.clear()

    def context_scope_changed(self, scope):
        if self.context_session is not None and self.context_session.scope != 'current_only':
            self.context_permissions.revoke_permission(self.context_session)
            self.context_latest_request = None
            self.context_suggestions.clear()
            if self.context_dialog is not None:
                self.context_dialog.show_suggestions(())

    def cancel_context_request(self):
        if self.context_session is not None:
            # Invalidate the worker result and its execution-time permission check.
            self.context_session.permission_version += 1
            self.context_latest_request = None
            self.context_suggestions.clear()

    def remove_context_source(self, memory_id):
        if self.context_session is not None:
            self.context_permissions.exclude_source(self.context_session, memory_id)
            self.context_latest_request = None
            self.context_suggestions.clear()
            if self.context_dialog is not None:
                self.context_dialog.show_suggestions(())

    def clear_context_session(self):
        if self.context_session is not None:
            reference = self.context_session.screenshot_reference
            self.context_permissions.revoke_permission(self.context_session)
            self.context_session = ContextSession.new(reference)
            self.context_latest_request = None
            self.context_suggestions.clear()

    def close_context_session(self):
        if self.context_session is not None:
            self.context_permissions.revoke_permission(self.context_session)
        self.context_session = None
        self.context_dialog = None
        self.context_latest_request = None
        self.context_suggestions.clear()

    def save_current_to_memory(self) -> None:
        if (not self.config.visual_memory_enabled or not self.config.show_memory_save_button or
                not isinstance(self.last_result, (SkillResult, ModeResult)) or self.quitting):
            return
        if (isinstance(self.last_result, ModeResult) and
                self.last_result.mode != 'extract' and self.last_result.data is not None):
            return
        preview = MemorySaveDialog(self.last_result, has_screenshot=bool(self.image_bytes),
                                   parent=self.result)
        if preview.exec() != QDialog.DialogCode.Accepted:
            return
        values = preview.values()
        note = values.pop('note', None)
        source_result = self.last_result
        if isinstance(source_result, ModeResult) and source_result.data is None:
            if not note:
                QMessageBox.warning(self.result, 'Visual Memory', 'Write a note before saving.')
                return
            source_result = ModeResult('manual_note', '', {'note': note})
        version_id = (self.editable_extraction.skill_version_id
                      if isinstance(self.last_result, SkillResult) and self.editable_extraction else None)
        try:
            try:
                record = self.memory_store.save_result(source_result, **values,
                    screenshot=self.image_bytes or None, skill_version_id=version_id,
                    source_created_at=self.capture_created_at)
            except DuplicateMemoryError as duplicate:
                prompt = QMessageBox(self.result)
                prompt.setWindowTitle('Already saved?')
                prompt.setText('This exact screenshot may already be saved.')
                open_button = prompt.addButton('Open Existing', QMessageBox.ButtonRole.AcceptRole)
                another_button = prompt.addButton('Save Another Copy', QMessageBox.ButtonRole.ActionRole)
                prompt.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
                prompt.exec()
                if prompt.clickedButton() == open_button:
                    self.open_memory()
                    self.open_memory_record(duplicate.existing_id)
                    return
                if prompt.clickedButton() != another_button:
                    return
                record = self.memory_store.save_result(source_result, **values,
                    screenshot=self.image_bytes or None, skill_version_id=version_id,
                    source_created_at=self.capture_created_at, allow_duplicate=True)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self.result, 'Unable to save Memory', str(exc))
            return
        self.result.set_notice('Saved to Visual Memory locally.')
        self.main_window.pages['memory'].refresh()

    def rebuild_memory_index(self):
        try:
            available = self.memory_store.rebuild_index()
        except (OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self.main_window, 'Memory search', str(exc))
            return
        self.main_window.toast.show_message(
            'Memory search index rebuilt.' if available else
            'Full-text indexing is unavailable; local fallback search remains available.')
        self.main_window.pages['memory'].refresh()

    def ask_memory(self, question: str, filters):
        if (self.quitting or not self.config.visual_memory_enabled or
                not self.config.ai_memory_questions or self.memory_recall_worker is not None):
            return
        page = self.main_window.pages['memory']
        page.set_answer_pending()
        worker = MemoryRecallWorker(self.memory_store, self.client, question, filters)
        worker.signals.finished.connect(self.memory_recall_finished)
        self.memory_recall_worker = worker
        self.memory_pool.start(worker)

    @Slot(object, str)
    def memory_recall_finished(self, answer, error):
        self.memory_recall_worker = None
        if self.quitting:
            QTimer.singleShot(0, self.finish_quit)
            return
        self.main_window.pages['memory'].show_answer(answer, error)

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
            dialog = GoogleConnectDialog(self.main_window,
                capabilities=connection.granted_capabilities if connection else ())
            dialog.submitted.connect(lambda capabilities:
                self._start_integration_worker('google', 'connect_google', capabilities))
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
        self._refresh_google_calendar_action()
        if not self.quitting:
            self.main_window.toast.show_message(outcome.message)

    def _google_calendar_connected(self):
        connection = self.connection_storage.get('google')
        return bool(connection and connection.status == 'connected' and
                    'google_calendar' in connection.granted_capabilities)

    def _refresh_google_calendar_action(self):
        self.result.set_google_calendar_connected(self._google_calendar_connected())
        self.result.set_calendar_action_busy(self.calendar_event_worker is not None)

    def create_calendar_event(self, result):
        if self.quitting or self.calendar_event_worker is not None or self.last_result is not result:
            return
        if not self._google_calendar_connected():
            self._refresh_google_calendar_action()
            self.open_integrations()
            return
        worker = CalendarEventWorker(self.integration_service, result)
        self.calendar_event_worker = worker
        self._refresh_google_calendar_action()
        worker.signals.finished.connect(
            lambda outcome, source=result: self.calendar_event_finished(source, outcome))
        self.integration_pool.start(worker)

    def calendar_event_finished(self, source_result, outcome):
        self.calendar_event_worker = None
        self._refresh_google_calendar_action()
        if (not self.quitting and self.last_result is source_result and
                self.result.calendar_event_result is source_result):
            self.result.set_calendar_action_status(outcome.message)
        self.main_window.pages['integrations'].refresh()

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
        except Exception as exc:
            self.clear_overlays()
            if self._onboarding_capture_test:
                logging.getLogger(__name__).warning('Onboarding screen capture test failed.', exc_info=True)
                self.finish_onboarding_capture_test(
                    False, 'Unable to capture the desktop. Try again on an unlocked display.')
            else:
                self.report_error(exc, context='screenshot_capture')

    def clear_overlays(self) -> None:
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()
        self.capturing = False

    def cancel_selection(self) -> None:
        self.clear_overlays()
        if self._onboarding_capture_test:
            self.finish_onboarding_capture_test(False, 'Capture test cancelled. No image was saved or sent.')
            return
        if self._restore_home_after_capture:
            self.main_window.show()
            self._restore_home_after_capture = False
        if self.image_bytes:
            self.result.show()

    def selected(self, image: QImage) -> None:
        if getattr(self, 'context_dialog', None) is not None:
            self.context_dialog.close()
        self.clear_overlays()
        self.result.set_notice()
        self.ask_history.clear()
        self.last_result = None
        self.editable_extraction = None
        self.saved_example_id = None
        self.failed_question = None
        self.result.followup.clear()
        self.retry_skill = None
        self.capture_created_at = datetime.now(timezone.utc).isoformat()
        try:
            image = resize_if_needed(image, self.config.max_image_width)
            self.image_bytes = image_to_png_bytes(image)
        except ValueError as exc:
            if self._onboarding_capture_test:
                self.finish_onboarding_capture_test(False, str(exc))
            else:
                self.report_error(exc, context='screenshot_capture')
            return
        if self._onboarding_capture_test:
            self.finish_onboarding_capture_test(
                True, 'Capture test succeeded. The selected screenshot was discarded and not sent to an AI service.')
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
        if self.context_dialog is not None:
            self.context_dialog.close()
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
        if self.context_dialog is not None:
            self.context_dialog.close()
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
        if self.context_dialog is not None:
            self.context_dialog.close()
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
                    self._refresh_google_calendar_action()
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
        self._refresh_google_calendar_action()
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
                self.feedback_storage = FeedbackStorage(path=self.paths.learning_database)
            record = self.feedback_storage.save_verified(self.editable_extraction, screenshot=screenshot)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self.result, 'Unable to save example', str(exc))
            return
        self.saved_example_id = record.id
        self.result.set_skill_result(self.last_result, editable=True)
        self._refresh_google_calendar_action()
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
        if self.context_dialog is not None:
            self.context_dialog.close()
        self.retry_skill = None
        self.result.set_notice()
        self.image_bytes = b""
        self.capture_created_at = None
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
        self.settings = SettingsWindow(
            self.config, self.result,
            api_key_configured=bool(os.getenv("AI_API_KEY")),
            api_key_stored=self.api_key_stored)
        self.settings.submitted.connect(self.apply_settings)
        self.settings.remove_key_requested.connect(self.remove_saved_api_key)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def show_onboarding(self) -> None:
        if self.quitting or self.onboarding_dialog is not None:
            return
        dialog = OnboardingDialog(self.config, self.main_window, hotkey_error=self.hotkey_error)
        dialog.setup_requested.connect(self.save_onboarding_setup)
        dialog.connection_test_requested.connect(self.test_onboarding_connection)
        dialog.capture_test_requested.connect(self.test_onboarding_capture)
        dialog.privacy_settings_requested.connect(self.open_settings_from_onboarding)
        dialog.finished.connect(self.onboarding_finished)
        self.onboarding_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def test_onboarding_connection(self, key: str, base_url: str, model: str) -> None:
        dialog = self.onboarding_dialog
        if dialog is None or self.quitting:
            return
        if self.onboarding_connection_worker is not None:
            dialog.set_connection_test_result(False, 'A connection test is already running.')
            return
        key = key or os.getenv('AI_API_KEY', '')
        if not key:
            dialog.set_connection_test_result(False, 'Enter an API key before testing the connection.')
            return
        try:
            Config(ai_base_url=base_url, ai_model=model)
        except ValueError as exc:
            dialog.set_connection_test_result(False, str(exc))
            return
        worker = OnboardingConnectionWorker(self.client, key, base_url, model)
        self.onboarding_connection_worker = worker
        worker.signals.finished.connect(self.onboarding_connection_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(bool, str)
    def onboarding_connection_finished(self, succeeded: bool, message: str) -> None:
        self.onboarding_connection_worker = None
        if self.onboarding_dialog is not None:
            self.onboarding_dialog.set_connection_test_result(succeeded, message)

    def save_onboarding_setup(self, key: str, base_url: str, model: str) -> None:
        try:
            candidate = replace(self.config, ai_base_url=base_url, ai_model=model,
                                onboarding_complete=True)
        except ValueError as exc:
            QMessageBox.warning(self.onboarding_dialog, 'Invalid AI setup', str(exc))
            return
        previous_saved_key = None
        credential_updated = False
        try:
            if key:
                previous_saved_key = self.credential_service.get_ai_api_key()
                self.credential_service.set_ai_api_key(key)
                credential_updated = True
            save_config(candidate, self.paths.config_file)
        except (CredentialError, OSError, ValueError) as exc:
            if credential_updated:
                try:
                    if previous_saved_key:
                        self.credential_service.set_ai_api_key(previous_saved_key)
                    else:
                        self.credential_service.delete_ai_api_key()
                except CredentialError:
                    logging.getLogger(__name__).exception('Unable to roll back an AI key after setup failed.')
            QMessageBox.warning(self.onboarding_dialog, 'Unable to save AI setup', str(exc))
            return
        self.config = candidate
        self.main_window.config = candidate
        self.main_window.pages['settings'].refresh(candidate)
        self.main_window.pages['evaluation'].max_evaluation_cases = candidate.max_evaluation_cases
        os.environ['AI_BASE_URL'] = candidate.ai_base_url
        os.environ['AI_MODEL'] = candidate.ai_model
        if key:
            os.environ['AI_API_KEY'] = key
            self.api_key_source = 'credential'
            self.api_key_stored = True
        if self.onboarding_dialog:
            self.onboarding_dialog.accept()

    def test_onboarding_capture(self) -> None:
        dialog = self.onboarding_dialog
        if dialog is None or self.quitting:
            return
        if self.capturing or self.workers:
            dialog.set_capture_test_result(False, 'Finish the current capture or AI request before testing capture.')
            return
        self._onboarding_capture_test = True
        dialog.hide()
        self.capture()

    def finish_onboarding_capture_test(self, succeeded: bool, message: str) -> None:
        self._onboarding_capture_test = False
        self.image_bytes = b''
        if self._restore_home_after_capture:
            self.main_window.show()
            self._restore_home_after_capture = False
        self.result.hide()
        dialog = self.onboarding_dialog
        if dialog is not None and not self.quitting:
            dialog.set_capture_test_result(succeeded, message)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def open_settings_from_onboarding(self) -> None:
        if self.onboarding_dialog is not None:
            self.onboarding_dialog.reject()
        self.open_settings()

    def onboarding_finished(self, result: int) -> None:
        if result == QDialog.DialogCode.Rejected:
            self.mark_onboarding_complete()
        self.onboarding_dialog = None

    def mark_onboarding_complete(self) -> None:
        completed = replace(self.config, onboarding_complete=True)
        try:
            save_config(completed, self.paths.config_file)
        except OSError as exc:
            QMessageBox.warning(self.main_window, "Unable to save setup status", str(exc))
        self.config = completed
        self.main_window.config = completed


    def remove_saved_api_key(self) -> None:
        if not self.api_key_stored:
            return
        answer = QMessageBox.question(
            self.settings,
            "Remove saved API key?",
            "Remove the saved AI API key from Windows Credential Manager?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.credential_service.delete_ai_api_key()
        except CredentialError as exc:
            QMessageBox.warning(self.settings, "Unable to remove API key", str(exc))
            return
        self.api_key_stored = False
        if self.api_key_source == "credential":
            if self._environment_api_key:
                os.environ["AI_API_KEY"] = self._environment_api_key
                self.api_key_source = "environment"
            else:
                os.environ.pop("AI_API_KEY", None)
                self.api_key_source = ""
        if self.settings:
            self.settings.key.clear()
            self.settings.refresh_api_key_state(
                configured=bool(os.getenv("AI_API_KEY")), stored=False)

    def export_backup(self) -> None:
        self.paths.backup_dir.mkdir(parents=True, exist_ok=True)
        default_name = self.paths.backup_dir / f"SnapFlow-Backup-{datetime.now():%Y-%m-%d}.zip"
        destination, _ = QFileDialog.getSaveFileName(
            self.main_window, "Export SnapFlow Backup", str(default_name), "SnapFlow Backup (*.zip)")
        if not destination:
            return
        try:
            result = self.backup_service.export_backup(destination)
        except (BackupError, OSError) as exc:
            QMessageBox.warning(self.main_window, "Unable to export backup", str(exc))
            return
        QMessageBox.information(self.main_window, "Backup exported", f"Backup saved to:\n{result}")

    def restore_backup(self) -> None:
        if self.capturing or self.workers or self.workflow_jobs or self.integration_jobs:
            QMessageBox.warning(
                self.main_window,
                "Finish active work first",
                "Wait for screenshot analysis, Workflow runs, and integration requests to finish before restoring data.",
            )
            return
        source, _ = QFileDialog.getOpenFileName(
            self.main_window, "Choose a SnapFlow Backup", str(self.paths.backup_dir), "SnapFlow Backup (*.zip)")
        if not source:
            return
        try:
            inspection = self.backup_service.inspect_backup(source)
        except BackupError as exc:
            QMessageBox.warning(self.main_window, "Backup could not be validated", str(exc))
            return
        categories = ", ".join(item.replace("_", " ").title() for item in inspection.categories)
        total_mb = inspection.total_size_bytes / (1024 * 1024)
        answer = QMessageBox.question(
            self.main_window,
            "Restore SnapFlow Backup?",
            f"This backup contains {inspection.file_count} files ({total_mb:.1f} MB):\n{categories or 'No categories'}\n\n"
            "A backup of your current data will be created first. API keys and OAuth credentials are not included. "
            "SnapFlow will close after restoring so it can load the restored data. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.backup_service.restore_backup(source)
        except BackupError as exc:
            QMessageBox.critical(self.main_window, "Restore failed", str(exc))
            return
        QMessageBox.information(
            self.main_window,
            "Backup restored",
            f"SnapFlow restored your data. Your previous data is backed up at:\n{result.current_backup}\n\n"
            "SnapFlow will now close. Reopen it to use the restored data.",
        )
        self.quit()

    def open_data_folder(self) -> None:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.data_dir)))

    def refresh_storage_usage(self) -> None:
        try:
            size = self.backup_service.storage_usage()
        except OSError as exc:
            QMessageBox.warning(self.main_window, "Unable to read storage usage", str(exc))
            return
        self.main_window.pages["settings"].set_storage_usage(size)

    def run_health_check(self) -> None:
        if self.health_check_worker is not None or self.quitting:
            return
        page = self.main_window.pages.get('settings')
        if page is not None:
            page.set_health_check_running(True)
        worker = BasicHealthCheckWorker(self.diagnostics_service)
        self.health_check_worker = worker
        worker.signals.finished.connect(self.health_check_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def health_check_finished(self, result) -> None:
        self.health_check_worker = None
        page = self.main_window.pages.get('settings')
        if page is not None:
            page.set_health_check_result(result)

    def record_api_usage(self, event) -> None:
        try:
            self.usage_storage.record(event)
            summary = self.usage_storage.monthly_summary(session_start=self.usage_session_started)
            self.api_usage_changed.emit(summary)
            self.maybe_warn_monthly_cost(summary)
        except (OSError, ValueError, sqlite3.Error):
            logging.getLogger(__name__).warning("Unable to save provider token usage.")

    def maybe_warn_monthly_cost(self, summary) -> None:
        threshold = self.config.monthly_cost_warning_usd
        if (threshold > 0 and summary.estimated_cost_usd is not None
                and summary.estimated_cost_usd >= threshold
                and self.usage_storage.claim_monthly_warning(summary.month)):
            self.monthly_cost_warning.emit(summary.estimated_cost_usd)

    def refresh_usage_summary(self) -> None:
        try:
            summary = self.usage_storage.monthly_summary(session_start=self.usage_session_started)
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).warning("Unable to load provider token usage.", exc_info=True)
            return
        self.update_usage_summary(summary)
        self.maybe_warn_monthly_cost(summary)

    @Slot(object)
    def update_usage_summary(self, summary) -> None:
        page = self.main_window.pages.get("settings") if hasattr(self, "main_window") else None
        if page is not None:
            page.set_usage_summary(summary, self.config.monthly_cost_warning_usd)

    @Slot(float)
    def show_monthly_cost_warning(self, estimated_cost) -> None:
        self.tray.showMessage(
            f"{APP_NAME} usage warning",
            f"Estimated AI spend this month has reached ${estimated_cost:.2f}. "
            "Check provider billing for the actual amount.")

    def check_for_updates(self, manual=True) -> None:
        if self.update_worker is not None:
            return
        page = self.main_window.pages.get('settings')
        if page is not None:
            page.set_update_status('Checking the configured GitHub Releases source…', checking=True)
        worker = UpdateCheckWorker()
        self.update_worker = worker
        worker.signals.finished.connect(self.update_check_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, str)
    def update_check_finished(self, result, error) -> None:
        self.update_worker = None
        page = self.main_window.pages.get('settings')
        if page is not None:
            page.set_update_status('Unable to check for updates. Check your connection and try again.')
        if result is None:
            return
        if result.error_code == 'no_release':
            message = 'No published release is available from the configured source yet.'
        elif result.error_code:
            message = 'Unable to check for updates. Check your connection and try again.'
        elif result.release and result.release.is_newer:
            release = result.release
            message = f'Version {release.tag_name} is available.'
            if page is not None:
                page.set_update_status(message)
            dialog = UpdateDialog(release, self.main_window)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                opened = QDesktopServices.openUrl(QUrl(official_release_page()))
                if not opened and page is not None:
                    page.set_update_status('The release page could not be opened. Visit the SnapFlow releases page on GitHub.')
            return
        else:
            message = f'{APP_NAME} {APP_VERSION} is up to date.'
        if page is not None:
            page.set_update_status(message)

    def export_support_bundle(self) -> None:
        self.paths.backup_dir.mkdir(parents=True, exist_ok=True)
        default_name = self.paths.backup_dir / f"SnapFlow-Support-{datetime.now():%Y-%m-%d}.zip"
        destination, _ = QFileDialog.getSaveFileName(
            self.main_window, "Export Support Bundle", str(default_name), "ZIP archive (*.zip)")
        if not destination:
            return
        answer = QMessageBox.question(
            self.main_window,
            "Export sanitized diagnostics?",
            "The local ZIP contains SnapFlow version and OS/Python/Qt details plus sanitized application logs. "
            "It excludes screenshots, extracted data, settings, Workflows, databases, API keys, and integration credentials. "
            "SnapFlow will not send it anywhere. Review it before sharing. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            bundle = self.diagnostics_service.export_bundle(destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.main_window, "Unable to export support bundle", str(exc))
            return
        QMessageBox.information(self.main_window, "Support bundle exported", f"Bundle saved to:\n{bundle}")

    def apply_settings(self, config: Config, key: str) -> None:
        previous = self.config
        if key and not config.onboarding_complete:
            config = replace(config, onboarding_complete=True)
        previous_saved_key = None
        credential_updated = False
        try:
            self.hotkeys.register(config.hotkey)
            if key:
                previous_saved_key = self.credential_service.get_ai_api_key()
                self.credential_service.set_ai_api_key(key)
                credential_updated = True
            save_config(config, self.paths.config_file)
        except (CredentialError, OSError, ValueError) as exc:
            if credential_updated:
                try:
                    if previous_saved_key:
                        self.credential_service.set_ai_api_key(previous_saved_key)
                    else:
                        self.credential_service.delete_ai_api_key()
                except CredentialError:
                    logging.getLogger(__name__).exception("Unable to roll back an API key after settings save failed.")
            try:
                self.hotkeys.register(previous.hotkey)
            except ValueError:
                logging.getLogger(__name__).exception("Unable to restore the previous hotkey after settings save failed.")
            QMessageBox.warning(self.settings, "Unable to save settings", str(exc))
            return
        self.config = config
        self.main_window.config = config
        if config.ai_base_url is not None:
            os.environ['AI_BASE_URL'] = config.ai_base_url
        if config.ai_model is not None:
            os.environ['AI_MODEL'] = config.ai_model
        self.main_window.pages['settings'].refresh(config)
        self.main_window.pages['memory'].set_ai_enabled(
            config.visual_memory_enabled and config.ai_memory_questions)
        self.result.memory_save_enabled = (
            config.visual_memory_enabled and config.show_memory_save_button)
        self.result.context_enabled = config.contextual_assistant_enabled
        if self.context_dialog and (not config.contextual_assistant_enabled or
                not config.visual_memory_enabled or
                (self.context_session and self.context_session.scope == 'authorized_memory_search'
                 and not config.context_memory_search_enabled)):
            self.context_dialog.close()
        self.main_window.pages['evaluation'].max_evaluation_cases = config.max_evaluation_cases
        self.theme_manager.set_preference(config.theme)
        self.main_window.hotkey_label.setText(' + '.join(part.capitalize() for part in config.hotkey.split('+')))
        if key:
            os.environ["AI_API_KEY"] = key
            self.api_key_source = "credential"
            self.api_key_stored = True
        self.settings.refresh_api_key_state(
            configured=bool(os.getenv("AI_API_KEY")), stored=self.api_key_stored)
        self.result.set_focus_topmost_enabled(config.always_on_top)
        self.settings.key.clear()
        self.settings.accept()
        self.refresh_usage_summary()
        self.change_mode(config.default_mode)
        if config.automatic_update_checks and not previous.automatic_update_checks:
            QTimer.singleShot(1000, self.check_for_updates)

    def report_error(self, error: Exception, *, context=None) -> None:
        report = classify_error(error, context=context)
        logging.getLogger(__name__).error(
            'User-visible failure category=%s reference=%s',
            report.category, report.reference_id,
            exc_info=(type(error), error, error.__traceback__))
        self.show_error(f'{report.user_message}\nReference ID: {report.reference_id}')

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
        if self.workers or self.context_workers:
            self.result.set_response("Finishing the current request before quitting...", error=True)
            self.result.show()
            self.capture_action.setEnabled(False)
        else:
            self.finish_quit()

    def finish_quit(self) -> None:
        if (QThreadPool.globalInstance().activeThreadCount() or self.workflow_jobs or
                self.integration_jobs or self.calendar_event_worker is not None or
                self.memory_recall_worker is not None or
                self.context_workers):
            self.result.set_response('Finishing the current skill request before quitting...', error=True)
            self.result.show()
            QTimer.singleShot(50, self.finish_quit)
            return
        self.pool.waitForDone()
        self.workflow_pool.waitForDone()
        self.integration_pool.waitForDone()
        self.memory_pool.waitForDone()
        self.context_pool.waitForDone()
        self.tray.hide()
        self.app.quit()
