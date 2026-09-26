"""Readable current-settings summary; existing editor performs changes."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel

from src.ui.design.components import AppButton, PageHeader
from src.ui.design.components import Card
from .common import ScrollPage


class SettingsPage(ScrollPage):
    edit_requested = Signal()

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.edit_button = AppButton('Edit Settings', variant='primary')
        self.edit_button.clicked.connect(self.edit_requested)
        self.content.addWidget(PageHeader('Settings',
            'Control capture, AI, and appearance preferences.', self.edit_button))
        capture = Card()
        capture.content.addWidget(QLabel('Capture'))
        capture.content.addWidget(QLabel('Hotkey'))
        self.hotkey_label = QLabel()
        capture.content.addWidget(self.hotkey_label)
        self.content.addWidget(capture)
        ai = Card()
        ai.content.addWidget(QLabel('AI'))
        ai.content.addWidget(QLabel('Default mode'))
        self.mode_label = QLabel()
        ai.content.addWidget(self.mode_label)
        self.content.addWidget(ai)
        appearance = Card()
        appearance.content.addWidget(QLabel('Appearance'))
        self.theme_label = QLabel()
        appearance.content.addWidget(self.theme_label)
        self.content.addWidget(appearance)
        privacy = Card()
        privacy.content.addWidget(QLabel('Privacy'))
        note = QLabel('Screenshots are kept in memory. Selected images are sent to your configured AI provider. Workflow History stores status only.')
        note.setWordWrap(True)
        note.setProperty('role', 'muted')
        privacy.content.addWidget(note)
        self.content.addWidget(privacy)
        self.content.addStretch(1)
        self.refresh(config)

    def refresh(self, config):
        if config is None:
            return
        self.hotkey_label.setText(' + '.join(part.capitalize() for part in config.hotkey.split('+')))
        self.mode_label.setText(config.default_mode.title())
        self.theme_label.setText(config.theme.title())
