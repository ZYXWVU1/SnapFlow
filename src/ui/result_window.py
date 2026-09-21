from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from src.prompts import MODES
from src.actions import copy_formats
from src.modes import ModeResult
from src.ui.structured_result import StructuredResult


class ResultWindow(QWidget):
    ask = Signal(str)
    mode_changed = Signal(str)
    closed = Signal()
    settings_requested = Signal()
    ask_ai = Signal(str)

    def __init__(self, mode: str, always_on_top: bool) -> None:
        super().__init__()
        self.setWindowTitle("AI Screenshot Helper")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
        self.resize(680, 600)
        self.setMinimumSize(340, 260)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        for value, label in MODES.items():
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        top.addWidget(QLabel("Mode"))
        top.addWidget(self.mode)
        top.addStretch()
        settings = QPushButton("Settings")
        settings.clicked.connect(self.settings_requested)
        top.addWidget(settings)
        layout.addLayout(top)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.hide()
        layout.addWidget(self.preview)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        self.structured = StructuredResult()
        self.structured.hide()
        layout.addWidget(self.structured)
        self.actions_layout = QGridLayout()
        self.action_buttons = {}
        layout.addLayout(self.actions_layout)
        self.followup_row = QWidget()
        followup_layout = QHBoxLayout(self.followup_row)
        followup_layout.setContentsMargins(0, 0, 0, 0)
        self.followup = QLineEdit()
        self.followup.setPlaceholderText('Ask a follow-up about this screenshot…')
        self.send = QPushButton('Send')
        followup_layout.addWidget(self.followup)
        followup_layout.addWidget(self.send)
        layout.addWidget(self.followup_row)
        self.followup_row.hide()
        self.send.clicked.connect(self.send_followup)
        self.followup.returnPressed.connect(self.send_followup)
        buttons = QHBoxLayout()
        self.copy = QPushButton("Copy")
        self.again = QPushButton("Ask Again")
        close = QPushButton("Close")
        for button in (self.copy, self.again, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        self.again.clicked.connect(self.ask_again)
        close.clicked.connect(self.close)
        self.mode.currentIndexChanged.connect(lambda: self.mode_changed.emit(self.mode.currentData()))

    def clear_actions(self):
        while self.actions_layout.count():
            widget = self.actions_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.action_buttons.clear()

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
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, text=value: QApplication.clipboard().setText(text))
            self.add_action(label, button)
        ask = QPushButton('Ask AI')
        ask.clicked.connect(lambda: self.ask_ai.emit(''))
        self.add_action('Ask AI', ask)
        if result.mode == 'debug':
            why = QPushButton('Explain Why')
            why.clicked.connect(lambda: self.ask_ai.emit('Explain why this problem occurs and how the suggested fix works.'))
            self.add_action('Explain Why', why)

    def add_action(self, label, button):
        index = len(self.action_buttons)
        self.action_buttons[label] = button
        self.actions_layout.addWidget(button, index // 3, index % 3)

    def set_busy(self) -> None:
        self.clear_actions()
        self.structured.hide()
        self.followup_row.hide()
        self.text.show()
        self.copy.show()
        self.send.setEnabled(False)
        self.preview.hide()
        self.text.setPlainText("Analyzing screenshot...")
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
