"""Human-readable Workflow overview with local search."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from src.ui.design.components import AppButton, Badge, PageHeader
from src.skill_actions import ACTIONS
from .common import NamedCard, ScrollPage


def step_label(step):
    action_id = getattr(step, 'action_id', '')
    return ACTIONS[action_id].label if action_id in ACTIONS else action_id or 'Action'


class WorkflowsPage(ScrollPage):
    manage_requested = Signal()
    edit_requested = Signal(str)
    test_requested = Signal(str)

    def __init__(self, storage=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.cards = []
        self.manage_button = AppButton('New Workflow', variant='primary')
        self.manage_button.clicked.connect(self.manage_requested)
        self.content.addWidget(PageHeader('Workflows',
            'Choose what happens after a Visual Skill matches.', self.manage_button))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search workflows')
        self.search.setAccessibleName('Search workflows')
        self.search.textChanged.connect(self._filter)
        self.content.addWidget(self.search)
        self.list = QVBoxLayout()
        self.content.addLayout(self.list)
        self.empty_state = NamedCard('No workflows yet',
            'Create a Workflow to turn a Skill result into useful actions.')
        start = AppButton('Create Workflow')
        start.clicked.connect(self.manage_requested)
        self.empty_state.content.addWidget(start)
        self.content.addWidget(self.empty_state)
        self.content.addStretch(1)
        self.refresh()

    def refresh(self):
        for card in self.cards:
            card.hide()
            self.list.removeWidget(card)
            card.deleteLater()
        self.cards = []
        for workflow in self.storage.list_workflows() if self.storage else ():
            card = NamedCard(workflow.name, f'WHEN {workflow.trigger.skill_id.replace("_", " ").title()} matches')
            card.content.addWidget(QLabel(f'THEN {len(workflow.steps)} actions'))
            summary = QLabel('\n'.join(f'{index}. {step_label(step)}'
                for index, step in enumerate(workflow.steps, 1)))
            summary.setWordWrap(True)
            summary.setProperty('role', 'muted')
            card.content.addWidget(summary)
            card.content.addWidget(Badge('Auto' if workflow.auto_run else 'Suggest'),
                                   alignment=Qt.AlignmentFlag.AlignLeft)
            card.content.addWidget(Badge('Enabled' if workflow.enabled else 'Disabled',
                                         status='success' if workflow.enabled else 'neutral'),
                                   alignment=Qt.AlignmentFlag.AlignLeft)
            actions = QHBoxLayout()
            card.test_button = AppButton('Run Test')
            card.edit_button = AppButton('Edit')
            card.test_button.clicked.connect(lambda checked=False, key=workflow.id: self.test_requested.emit(key))
            card.edit_button.clicked.connect(lambda checked=False, key=workflow.id: self.edit_requested.emit(key))
            actions.addWidget(card.test_button)
            actions.addWidget(card.edit_button)
            actions.addStretch(1)
            card.content.addLayout(actions)
            self.list.addWidget(card)
            self.cards.append(card)
        self._filter(self.search.text())

    def _filter(self, query):
        query = query.strip().casefold()
        visible = False
        for card in self.cards:
            match = query in card.name.casefold()
            card.setVisible(match)
            visible |= match
        self.empty_state.setVisible(not visible)

    def visible_cards(self):
        return [card for card in self.cards if not card.isHidden()]
