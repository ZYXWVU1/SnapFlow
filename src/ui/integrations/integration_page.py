"""Registry-driven account overview without access to credential values."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from src.ui.design.components import AppButton, Badge, PageHeader
from src.ui.pages.common import NamedCard, ScrollPage


class IntegrationCard(NamedCard):
    def __init__(self, definition, parent=None):
        super().__init__(definition.name, definition.description, parent)
        self.definition = definition
        self.capabilities = QLabel(' · '.join(item.replace('_', ' ').title()
                                            for item in definition.capabilities))
        self.capabilities.setProperty('role', 'muted')
        self.content.addWidget(self.capabilities)
        self.status_label = Badge('Not connected')
        self.content.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignLeft)
        self.account_label = QLabel()
        self.account_label.setProperty('role', 'muted')
        self.content.addWidget(self.account_label)
        buttons = QHBoxLayout()
        self.connect_button = AppButton('Connect', variant='primary')
        self.test_button = AppButton('Test Connection')
        self.disconnect_button = AppButton('Disconnect', variant='danger')
        for button in (self.connect_button, self.test_button, self.disconnect_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.content.addLayout(buttons)

    def update_state(self, connection):
        status = connection.status if connection else 'disconnected'
        label = {'disconnected': 'Not connected', 'connected': 'Connected',
                 'needs_reconnect': 'Needs reauthorization', 'error': 'Connection error'}[status]
        self.status_label.setText(label)
        self.status_label.setProperty('status',
            'success' if status == 'connected' else 'warning' if status == 'needs_reconnect' else 'neutral')
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.account_label.setText(connection.account_label or '' if connection else '')
        self.account_label.setVisible(bool(self.account_label.text()))
        self.connect_button.setText('Manage permissions' if status == 'connected' and
            self.definition.id == 'google' else 'Reconnect' if status == 'needs_reconnect' else 'Connect')
        self.connect_button.setVisible(status != 'connected' or self.definition.id == 'google')
        self.test_button.setVisible(status == 'connected')
        self.disconnect_button.setVisible(status != 'disconnected')


class IntegrationPage(ScrollPage):
    connection_requested = Signal(str)
    test_requested = Signal(str)
    disconnect_requested = Signal(str)

    def __init__(self, registry, storage, parent=None):
        super().__init__(parent)
        self.registry, self.storage = registry, storage
        self.content.addWidget(PageHeader('Integrations',
            'Connect Visual Workflow AI to the tools you already use.'))
        self.cards = {}
        for definition in registry.definitions():
            card = IntegrationCard(definition)
            card.connect_button.clicked.connect(
                lambda checked=False, key=definition.id: self.connection_requested.emit(key))
            card.test_button.clicked.connect(
                lambda checked=False, key=definition.id: self.test_requested.emit(key))
            card.disconnect_button.clicked.connect(
                lambda checked=False, key=definition.id: self.disconnect_requested.emit(key))
            self.content.addWidget(card)
            self.cards[definition.id] = card
        self.content.addStretch(1)
        self.refresh()

    def refresh(self):
        for key, card in self.cards.items():
            card.update_state(self.storage.get(key) if self.storage else None)

    def set_busy(self, integration_id, busy, message='Connecting…'):
        card = self.cards[integration_id]
        for button in (card.connect_button, card.test_button, card.disconnect_button):
            button.setEnabled(not busy)
        if busy:
            card.status_label.setText(message)
