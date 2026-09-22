import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QSpinBox

from src.config import Config
from src.prompts import MODES


class SettingsWindow(QDialog):
    submitted = Signal(object, str)

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, config.always_on_top)
        self.config = config
        self.setWindowTitle("AI Screenshot Helper - Settings")
        self.setMinimumWidth(440)
        form = QFormLayout(self)
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
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Configured; blank keeps current key" if os.getenv("AI_API_KEY") else "Enter API key for this session")
        form.addRow("Hotkey", self.hotkey)
        form.addRow("Default mode", self.mode)
        form.addRow("Maximum image width", self.width)
        form.addRow(self.on_top)
        form.addRow("API key", self.key)
        note = QLabel("The key entered here is kept for this session only.\nFor persistent setup, set AI_API_KEY in .env.\nSelected screenshots are sent to the configured AI service.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def submit(self) -> None:
        try:
            config = Config(self.hotkey.text().strip().lower(), self.mode.currentData(), self.width.value(),
                            self.on_top.isChecked(), self.config.smart_classification_threshold)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        self.submitted.emit(config, self.key.text().strip())
