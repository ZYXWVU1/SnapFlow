"""Metadata-only Workflow history page."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from src.ui.design.components import Badge, PageHeader
from .common import NamedCard, ScrollPage


class HistoryPage(ScrollPage):
    def __init__(self, history=None, parent=None):
        super().__init__(parent)
        self.history = history
        self.entries = []
        self.content.addWidget(PageHeader('History', 'Recent Workflow outcomes on this device.'))
        self.list = QVBoxLayout()
        self.content.addLayout(self.list)
        self.empty_state = NamedCard('No workflow activity yet',
            'Workflow outcomes will appear here after an action runs.')
        self.content.addWidget(self.empty_state)
        self.content.addStretch(1)
        self.refresh()

    def refresh(self):
        for card in self.entries:
            card.hide()
            self.list.removeWidget(card)
            card.deleteLater()
        self.entries = []
        for entry in reversed(self.history.list_entries() if self.history else []):
            card = NamedCard(entry['workflow_name'], entry['started_at'])
            status = entry['status']
            card.content.addWidget(Badge(status.replace('_', ' ').title(),
                status='success' if status == 'success' else 'error' if status == 'failed' else 'neutral'),
                alignment=Qt.AlignmentFlag.AlignLeft)
            card.content.addWidget(QLabel(f"{len(entry['steps'])} actions"))
            self.list.addWidget(card)
            self.entries.append(card)
        self.empty_state.setVisible(not self.entries)
