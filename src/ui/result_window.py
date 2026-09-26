from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget

from src.prompts import MODES
from src.actions import copy_formats
from src.modes import ModeResult
from src.ui.structured_result import StructuredResult
from src.skill_actions import ACTIONS
from src.ui.skill_view import render_skill
from src.ui.skill_actions import run_action
from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


class ResultWindow(QWidget):
    ask = Signal(str)
    mode_changed = Signal(str)
    closed = Signal()
    settings_requested = Signal()
    ask_ai = Signal(str)
    workflow_requested = Signal(str)
    workflow_cancel_requested = Signal()
    integration_requested = Signal()

    def __init__(self, mode: str, always_on_top: bool) -> None:
        super().__init__()
        self.setWindowTitle("Visual Workflow AI")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
        self.resize(720, 680)
        self.setMinimumSize(340, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        layout.setSpacing(SPACING['md'])
        layout.addWidget(PageHeader('Screenshot result', 'Review what the app understood and choose what happens next.'))
        top = QHBoxLayout()
        self.mode = QComboBox()
        for value, label in MODES.items():
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        top.addWidget(QLabel("Mode"))
        top.addWidget(self.mode)
        top.addStretch()
        settings = AppButton("Settings", variant='ghost')
        settings.clicked.connect(self.settings_requested)
        top.addWidget(settings)
        layout.addLayout(top)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setProperty('role', 'badge')
        self.notice.hide()
        layout.addWidget(self.notice)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        scroll_body = QWidget()
        self.content_scroll.setWidget(scroll_body)
        body = QVBoxLayout(scroll_body)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        body.setSpacing(SPACING['lg'])
        layout.addWidget(self.content_scroll, 1)
        self.result_card = Card()
        body.addWidget(self.result_card)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.hide()
        self.result_card.content.addWidget(self.preview)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(180)
        self.result_card.content.addWidget(self.text)
        self.structured = StructuredResult()
        self.structured.hide()
        self.result_card.content.addWidget(self.structured)
        self.action_card = Card()
        self.action_card.content.addWidget(QLabel('Actions'))
        self.actions_layout = QGridLayout()
        self.action_buttons = {}
        self.action_card.content.addLayout(self.actions_layout)
        self.action_status = QLabel()
        self.action_status.setTextFormat(Qt.TextFormat.PlainText)
        self.action_status.setWordWrap(True)
        self.action_card.content.addWidget(self.action_status)
        body.addWidget(self.action_card)
        self.action_card.hide()
        self.workflow_registry = None
        self.workflow_buttons = []
        self.workflow_area = Card()
        self.workflow_layout = self.workflow_area.content
        self.workflow_heading = QLabel('Workflows')
        self.workflow_layout.addWidget(self.workflow_heading)
        self.workflow_status = QLabel()
        self.workflow_status.setTextFormat(Qt.TextFormat.PlainText)
        self.workflow_status.setWordWrap(True)
        self.workflow_layout.addWidget(self.workflow_status)
        self.integration_connect = AppButton('Open Integrations', variant='secondary')
        self.integration_connect.clicked.connect(self.integration_requested)
        self.integration_connect.hide()
        self.workflow_layout.addWidget(self.integration_connect)
        self.workflow_cancel = AppButton('Cancel Workflow', variant='secondary')
        self.workflow_cancel.clicked.connect(self.workflow_cancel_requested)
        self.workflow_cancel.hide()
        self.workflow_layout.addWidget(self.workflow_cancel)
        body.addWidget(self.workflow_area)
        self.workflow_area.hide()
        body.addStretch(1)
        self.followup_row = QWidget()
        followup_layout = QHBoxLayout(self.followup_row)
        followup_layout.setContentsMargins(0, 0, 0, 0)
        self.followup = QLineEdit()
        self.followup.setPlaceholderText('Ask a follow-up about this screenshot…')
        self.send = AppButton('Send', variant='primary')
        followup_layout.addWidget(self.followup)
        followup_layout.addWidget(self.send)
        layout.addWidget(self.followup_row)
        self.followup_row.hide()
        self.send.clicked.connect(self.send_followup)
        self.followup.returnPressed.connect(self.send_followup)
        buttons = QHBoxLayout()
        self.copy = AppButton("Copy")
        self.again = AppButton("Ask Again", variant='primary')
        close = AppButton("Close", variant='ghost')
        for button in (self.copy, self.again, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        self.again.clicked.connect(self.ask_again)
        close.clicked.connect(self.close)
        self.mode.currentIndexChanged.connect(lambda: self.mode_changed.emit(self.mode.currentData()))

    def clear_actions(self):
        self.action_status.clear()
        self.action_card.hide()
        self.clear_workflows()
        while self.actions_layout.count():
            widget = self.actions_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.action_buttons.clear()

    def set_workflow_registry(self, registry):
        self.workflow_registry = registry

    def clear_workflows(self):
        for button in self.workflow_buttons:
            self.workflow_layout.removeWidget(button)
            button.deleteLater()
        self.workflow_buttons.clear()
        self.workflow_status.clear()
        self.integration_connect.hide()
        self.workflow_cancel.hide()
        self.workflow_area.hide()

    def set_workflow_status(self, text, running=False):
        self.workflow_area.show()
        self.workflow_status.setText(text)
        self.integration_connect.setVisible('not connected' in text.casefold() or
            'reauthorization' in text.casefold() or 'reconnect' in text.casefold())
        self.workflow_cancel.setVisible(running)

    def set_skill_result(self, result):
        self.set_response('')
        self.again.setText('Retry')
        self.text.hide()
        self.copy.hide()
        render_skill(self.structured, result)
        self.structured.show()
        for action_id in result.actions:
            action = ACTIONS.get(action_id)
            if action is None:
                continue
            button = AppButton(action.label)
            button.setEnabled(action.enabled(result))
            if not button.isEnabled():
                button.setToolTip('Required information was not visible in the screenshot.')
            button.clicked.connect(lambda checked=False, key=action_id: run_action(self, key, result))
            self.add_action(action.label, button)
        if self.workflow_registry:
            workflows = self.workflow_registry.matching(result.skill_id)
            for workflow in workflows:
                if workflow.auto_run:
                    continue
                button = AppButton('Run ' + workflow.name, variant='primary')
                button.clicked.connect(lambda checked=False, key=workflow.id: self.workflow_requested.emit(key))
                self.workflow_layout.insertWidget(1 + len(self.workflow_buttons), button)
                self.workflow_buttons.append(button)
            if self.workflow_buttons:
                self.workflow_area.show()

    def set_skill_error(self, message):
        self.set_response(message, True)
        button = AppButton('Ask AI')
        button.clicked.connect(lambda: self.ask_ai.emit(''))
        self.add_action('Ask AI', button)

    def send_followup(self):
        question = self.followup.text().strip()
        if question and self.send.isEnabled():
            self.ask.emit(question)
            self.followup.clear()

    def set_result(self, result: ModeResult, conversation: str | None = None):
        self.set_response(conversation if conversation is not None else result.text)
        self.again.setText('Ask Again' if result.mode in ('explain', 'translate') else 'Retry')
        self.followup_row.setVisible(result.mode == 'ask')
        if result.data is None:
            return
        self.text.hide()
        self.copy.hide()
        self.structured.render(result)
        self.structured.show()
        for label, value in copy_formats(result).items():
            button = AppButton(label)
            button.clicked.connect(lambda checked=False, text=value: QApplication.clipboard().setText(text))
            self.add_action(label, button)
        ask = AppButton('Ask AI')
        ask.clicked.connect(lambda: self.ask_ai.emit(''))
        self.add_action('Ask AI', ask)
        if result.mode == 'debug':
            why = AppButton('Explain Why')
            why.clicked.connect(lambda: self.ask_ai.emit('Explain why this problem occurs and how the suggested fix works.'))
            self.add_action('Explain Why', why)

    def add_action(self, label, button):
        self.action_card.show()
        index = len(self.action_buttons)
        self.action_buttons[label] = button
        self.actions_layout.addWidget(button, index // 3, index % 3)

    def set_notice(self, text: str = '') -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def set_busy(self, message: str = 'Analyzing screenshot...') -> None:
        self.clear_actions()
        self.structured.hide()
        self.followup_row.hide()
        self.text.show()
        self.copy.show()
        self.send.setEnabled(False)
        self.preview.hide()
        self.text.setPlainText(message)
        self.copy.setEnabled(False)
        self.again.setEnabled(False)
        self.mode.setEnabled(False)

    def set_response(self, text: str, error: bool = False) -> None:
        if error:
            self.again.setText('Retry')
        self.clear_actions()
        self.structured.hide()
        self.followup_row.hide()
        self.preview.hide()
        self.text.show()
        self.copy.show()
        self.send.setEnabled(True)
        self.text.setPlainText(text)
        self.copy.setEnabled(not error)
        self.again.setEnabled(True)
        self.mode.setEnabled(True)

    def show_preview(self, image: QImage) -> None:
        self.set_response('')
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(460, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.preview.show()
        self.text.setPlainText("Preview mode: screenshot captured in memory. No AI request was sent.")
        self.copy.setEnabled(False)
        self.again.setEnabled(False)

    def ask_again(self) -> None:
        if self.again.text() == 'Retry':
            self.ask.emit('')
            return
        question, ok = QInputDialog.getMultiLineText(self, "Ask Again", "Question about this screenshot (blank retries the selected mode):")
        if ok:
            self.ask.emit(question)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)
