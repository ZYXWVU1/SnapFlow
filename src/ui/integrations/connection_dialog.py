"""Reviewable connection inputs; tokens are never shown again after submission."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QLineEdit, QVBoxLayout

from src.ui.design.components import AppButton, Card, PageHeader
from src.ui.design.tokens import SPACING


class GoogleConnectDialog(QDialog):
    submitted = Signal(str, object)

    def __init__(self, client_id='', parent=None, capabilities=()):
        super().__init__(parent)
        self.setWindowTitle('Connect Google')
        self.resize(500, 380)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        root.addWidget(PageHeader('Connect Google',
            'Choose where Visual Workflows may create data. Your browser will ask for consent.'))
        card = Card()
        card.content.addWidget(QLabel('Desktop OAuth client ID'))
        self.client_id = QLineEdit(client_id)
        self.client_id.setPlaceholderText('...apps.googleusercontent.com')
        self.client_id.setAccessibleName('Google Desktop OAuth client ID')
        card.content.addWidget(self.client_id)
        note = QLabel('Use a Desktop OAuth client from Google Cloud. This public ID is kept in Windows Credential Manager.')
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
        client_id = self.client_id.text().strip()
        capabilities = tuple(name for checked, name in (
            (self.calendar.isChecked(), 'google_calendar'),
            (self.sheets.isChecked(), 'google_sheets')) if checked)
        if not client_id.endswith('.apps.googleusercontent.com') or not capabilities:
            self.error.setText('Enter a Desktop OAuth client ID and choose at least one capability.')
            return
        self.error.clear()
        self.submitted.emit(client_id, capabilities)
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
