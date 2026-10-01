"""Reviewable connection inputs; tokens are never shown again after submission."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QLineEdit, QVBoxLayout

from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


class GoogleConnectDialog(QDialog):
    submitted = Signal(object)

    def __init__(self, parent=None, capabilities=()):
        super().__init__(parent)
        self.setWindowTitle('Connect Google')
        self.resize(500, 320)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Connect Google',
            'Sign in to Google, then approve the Calendar or Sheets access you choose.'))
        card = Card()
        note = QLabel('Google will show the requested permissions in your browser before connecting.')
        note.setWordWrap(True)
        note.setProperty('role', 'muted')
        card.content.addWidget(note)
        self.calendar = QCheckBox('Calendar: create events')
        self.calendar.setChecked('google_calendar' in capabilities or not capabilities)
        self.sheets = QCheckBox('Sheets: append rows')
        self.sheets.setChecked('google_sheets' in capabilities)
        card.content.addWidget(self.calendar)
        card.content.addWidget(self.sheets)
        root.addWidget(card)
        self.error = QLabel()
        self.error.setProperty('status', 'error')
        root.addWidget(self.error)
        self.connect_button = AppButton('Continue in Browser', variant='primary')
        self.connect_button.clicked.connect(self.submit)
        root.addWidget(self.connect_button)

    def submit(self):
        capabilities = tuple(name for checked, name in (
            (self.calendar.isChecked(), 'google_calendar'),
            (self.sheets.isChecked(), 'google_sheets')) if checked)
        if not capabilities:
            self.error.setText('Choose at least one Google service to connect.')
            return
        self.error.clear()
        self.submitted.emit(capabilities)
        self.accept()


class TodoistConnectDialog(QDialog):
    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Connect Todoist')
        self.resize(480, 270)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Connect Todoist', 'Create tasks from Visual Workflows.'))
        card = Card()
        card.content.addWidget(QLabel('Personal API token'))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setAccessibleName('Todoist API token')
        card.content.addWidget(self.token)
        note = QLabel('Find your token in Todoist Settings → Integrations → Developer. It is stored in Windows Credential Manager.')
        note.setWordWrap(True)
        note.setProperty('role', 'muted')
        card.content.addWidget(note)
        root.addWidget(card)
        self.error = QLabel()
        self.error.setProperty('status', 'error')
        root.addWidget(self.error)
        self.connect_button = AppButton('Connect', variant='primary')
        self.connect_button.clicked.connect(self.submit)
        root.addWidget(self.connect_button)

    def submit(self):
        token = self.token.text().strip()
        if not token:
            self.error.setText('Enter a Todoist API token.')
            return
        self.error.clear()
        self.submitted.emit(token)
        self.token.clear()
        self.accept()
