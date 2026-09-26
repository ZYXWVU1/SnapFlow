import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from src.config import Config
from src.prompts import MODES
from src.ui.design.components import Card, PageHeader
from src.ui.design.tokens import SPACING


class SettingsWindow(QDialog):
    submitted = Signal(object, str)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, config.always_on_top)
        self.config = config
        self.setWindowTitle("Visual Workflow AI - Settings")
        self.resize(560, 570)
        self.setMinimumSize(440, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Settings', 'Choose how capture, AI, and appearance work.'))
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, SPACING['sm'], 0)
        root.addWidget(self.content_scroll, 1)
        self.capture_card = Card()
        self.capture_card.content.addWidget(QLabel('Capture'))
        capture_form = QFormLayout()
        self.hotkey = QLineEdit(config.hotkey)
        self.mode = QComboBox()
        for value, label in MODES.items():
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(config.default_mode))
        self.width = QSpinBox()
        self.width.setRange(320, 8192)
        self.width.setValue(config.max_image_width)
        self.on_top = QCheckBox("Keep results above other windows")
        self.on_top.setChecked(config.always_on_top)
        self.theme = QComboBox()
        for value, label in (('system', 'System'), ('light', 'Light'), ('dark', 'Dark')):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(self.theme.findData(config.theme))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Configured; blank keeps current key" if os.getenv("AI_API_KEY") else "Enter API key for this session")
        capture_form.addRow("Hotkey", self.hotkey)
        capture_form.addRow("Maximum image width", self.width)
        capture_form.addRow(self.on_top)
        self.capture_card.content.addLayout(capture_form)
        body.addWidget(self.capture_card)
        self.ai_card = Card()
        self.ai_card.content.addWidget(QLabel('AI'))
        ai_form = QFormLayout()
        ai_form.addRow("Default mode", self.mode)
        ai_form.addRow("API key", self.key)
        self.ai_card.content.addLayout(ai_form)
        note = QLabel("The key entered here is kept for this session only.\nFor persistent setup, set AI_API_KEY in .env.\nSelected screenshots are sent to the configured AI service.")
        note.setWordWrap(True)
        note.setProperty('role', 'muted')
        self.ai_card.content.addWidget(note)
        body.addWidget(self.ai_card)
        self.appearance_card = Card()
        self.appearance_card.content.addWidget(QLabel('Appearance'))
        appearance_form = QFormLayout()
        appearance_form.addRow('Theme', self.theme)
        self.appearance_card.content.addLayout(appearance_form)
        body.addWidget(self.appearance_card)
        body.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def submit(self) -> None:
        try:
            config = Config(self.hotkey.text().strip().lower(), self.mode.currentData(), self.width.value(),
                            self.on_top.isChecked(), self.config.smart_classification_threshold, self.theme.currentData())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        self.submitted.emit(config, self.key.text().strip())
