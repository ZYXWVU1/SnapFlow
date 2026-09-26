"""Visual Skill overview inside the application shell."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from src.skills.registry import SKILLS
from src.ui.design.components import AppButton, Badge, Card, PageHeader
from .common import NamedCard, ScrollPage


class SkillsPage(ScrollPage):
    manage_requested = Signal()
    edit_requested = Signal(str)
    test_requested = Signal(str)

    def __init__(self, storage=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.cards = []
        self.manage_button = self._manage_button('Create Skill', 'primary')
        self.content.addWidget(PageHeader('Visual Skills',
            'Teach AI to recognize the screen content that matters to you.',
            self.manage_button))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search skills')
        self.search.setAccessibleName('Search skills')
        self.search.textChanged.connect(self._filter)
        self.content.addWidget(self.search)
        self.content.addWidget(QLabel('Built-in'))
        builtin_grid = QGridLayout()
        self.builtin_cards = []
        for index, skill in enumerate(SKILLS.values()):
            card = NamedCard(skill.title, 'Ready for Smart Mode')
            card.content.addWidget(Badge('Built-in'), alignment=Qt.AlignmentFlag.AlignLeft)
            builtin_grid.addWidget(card, index // 2, index % 2)
            self.builtin_cards.append(card)
        self.content.addLayout(builtin_grid)
        self.content.addWidget(QLabel('My Skills'))
        self.list = QVBoxLayout()
        self.content.addLayout(self.list)
        self.empty_state = NamedCard('No custom skills yet',
            'Create a Visual Skill to recognize screenshots specific to your work.')
        self.empty_state.content.addWidget(self._manage_button('Create Visual Skill', 'secondary'))
        self.content.addWidget(self.empty_state)
        self.content.addStretch(1)
        self.refresh()

    def _manage_button(self, text, variant):
        button = AppButton(text, variant=variant)
        button.clicked.connect(self.manage_requested)
        return button

    def refresh(self):
        for card in self.cards:
            card.hide()
            self.list.removeWidget(card)
            card.deleteLater()
        self.cards = []
        for skill in self.storage.list_skills() if self.storage else ():
            fields = len(getattr(skill, 'fields', ()))
            actions = len(getattr(skill, 'action_ids', getattr(skill, 'actions', ())))
            card = NamedCard(skill.name, getattr(skill, 'description', ''))
            card.content.addWidget(QLabel(f'{fields} fields · {actions} actions'))
            card.content.addWidget(Badge('Enabled' if skill.enabled else 'Disabled',
                                         status='success' if skill.enabled else 'neutral'),
                                   alignment=Qt.AlignmentFlag.AlignLeft)
            actions = QHBoxLayout()
            card.edit_button = AppButton('Edit')
            card.test_button = AppButton('Test')
            card.edit_button.clicked.connect(lambda checked=False, key=skill.id: self.edit_requested.emit(key))
            card.test_button.clicked.connect(lambda checked=False, key=skill.id: self.test_requested.emit(key))
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

    def visible_custom_cards(self):
        return [card for card in self.cards if not card.isHidden()]
