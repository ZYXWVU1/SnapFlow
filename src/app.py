"""Coordinate capture, background requests, and the tray lifecycle."""
import os
import logging
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
from src.smart.models import ClassificationResult, unknown
from src.smart.router import SmartRouter
from src.smart.worker import ClassificationWorker
from src.smart.presentation import detection_notice, loading_message
from src.skills.base import SkillResult
from src.skills.registry import SKILLS
from src.skills.worker import SkillExtractionWorker


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
        self.client = LLMClient()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.workers: dict[int, AnalysisWorker | ClassificationWorker | SkillExtractionWorker] = {}
        self.request_id = 0
        self.image_bytes = b""
        self.ask_history: list[dict[str, str]] = []
        self.last_result: ModeResult | SkillResult | None = None
        self.failed_question: str | None = None
        self.overlays: list[SelectionOverlay] = []
        self.capturing = False
        self.quitting = False
        self.settings: SettingsWindow | None = None
        self.result = ResultWindow(self.mode, self.config.always_on_top)
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
        self.tray.setToolTip("AI Screenshot Helper")
        self.menu = QMenu()
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

    def capture(self) -> None:
        if self.capturing or self.workers or self.quitting:
            return
        self.capturing = True
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
        if self.image_bytes:
            self.result.show()

    def selected(self, image: QImage) -> None:
        self.clear_overlays()
        self.result.set_notice()
        self.ask_history.clear()
        self.last_result = None
        self.failed_question = None
        self.result.followup.clear()
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
            self.request_id += 1
            worker = SkillExtractionWorker(self.request_id, self.client, self.image_bytes,
                                           SKILLS[route], classification.confidence)
            worker.signals.finished.connect(self.analysis_finished)
            self.workers[self.request_id] = worker
            self.result.set_busy(loading_message(route))
            self.pool.start(worker)
        else:
            self.start_analysis(route)

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
                if isinstance(worker, SkillExtractionWorker):
                    self.result.set_skill_error(text)
                else:
                    self.result.set_response(text, True)
            else:
                self.last_result = text
                if isinstance(text, SkillResult):
                    self.result.set_skill_result(text)
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
        self.result.set_notice()
        self.image_bytes = b""
        self.ask_history.clear()
        self.last_result = None
        self.failed_question = None
        self.result.followup.clear()
        self.request_id += 1

    def change_mode(self, mode: str, analyze: bool = True) -> None:
        if self.workers:
            return
        self.result.set_notice()
        self.failed_question = None
        self.mode = mode
        self.mode_actions[mode].setChecked(True)
        self.result.mode.blockSignals(True)
        self.result.mode.setCurrentIndex(self.result.mode.findData(mode))
        self.result.mode.blockSignals(False)
        if analyze and self.image_bytes and not self.workers:
            self.analyze()

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
        self.image_bytes = b""
        if self.settings:
            self.settings.close()
        if self.workers:
            self.result.set_response("Finishing the current request before quitting...", error=True)
            self.result.show()
            self.capture_action.setEnabled(False)
        else:
            self.finish_quit()

    def finish_quit(self) -> None:
        self.pool.waitForDone()
        self.tray.hide()
        self.app.quit()
