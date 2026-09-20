from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from src.prompts import MODES


class ResultWindow(QWidget):
    ask = Signal(str)
    mode_changed = Signal(str)
    closed = Signal()
    settings_requested = Signal()

    def __init__(self, mode: str, always_on_top: bool) -> None:
        super().__init__()
        self.setWindowTitle("AI Screenshot Helper")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, always_on_top)
        self.resize(520, 420)
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

    def set_busy(self) -> None:
        self.preview.hide()
        self.text.setPlainText("Analyzing screenshot...")
        self.copy.setEnabled(False)
        self.again.setEnabled(False)
        self.mode.setEnabled(False)

    def set_response(self, text: str, error: bool = False) -> None:
        self.text.setPlainText(text)
        self.copy.setEnabled(not error)
        self.again.setEnabled(True)
        self.mode.setEnabled(True)

    def show_preview(self, image: QImage) -> None:
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(460, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.preview.show()
        self.text.setPlainText("Preview mode: screenshot captured in memory. No AI request was sent.")
        self.copy.setEnabled(False)
        self.again.setEnabled(False)

    def ask_again(self) -> None:
        question, ok = QInputDialog.getMultiLineText(self, "Ask Again", "Question about this screenshot (blank retries the selected mode):")
        if ok:
            self.ask.emit(question)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)
