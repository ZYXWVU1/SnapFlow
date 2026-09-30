"""Local Visual Memory search page."""
from datetime import datetime, time, timedelta, timezone

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
import sqlite3
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QVBoxLayout, QDateEdit)

from src.ui.design.components import AppButton, Card, PageHeader
from .common import ScrollPage


class MemoryPage(ScrollPage):
    memory_open_requested = Signal(str)
    capture_requested = Signal()
    ask_requested = Signal(str, object)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.offset = 0
        self.content.addWidget(PageHeader('Visual Memory',
            'Search screenshots and extracted information you chose to save.'))
        search_card = Card()
        self.query = QLineEdit()
        self.query.setPlaceholderText('Search memories...')
        self.query.returnPressed.connect(self._restart)
        search_card.content.addWidget(self.query)
        filters = QHBoxLayout()
        self.source = QComboBox()
        for label, value in [('All types', ''), ('Built-in Skills', 'builtin_skill'),
                             ('Custom Skills', 'custom_skill'), ('Extract', 'extract')]:
            self.source.addItem(label, value)
        self.source.currentIndexChanged.connect(self._restart)
        filters.addWidget(self.source)
        self.date = QComboBox()
        for label, days in [('Any date', 0), ('Today', 1), ('Last 7 days', 7),
                            ('Last 30 days', 30), ('Custom range', -1)]:
            self.date.addItem(label, days)
        self.date.currentIndexChanged.connect(self._restart)
        filters.addWidget(self.date)
        search_card.content.addLayout(filters)
        custom_dates = QHBoxLayout()
        self.date_from = QDateEdit(QDate.currentDate())
        self.date_to = QDateEdit(QDate.currentDate())
        for editor in (self.date_from, self.date_to):
            editor.setCalendarPopup(True)
            editor.dateChanged.connect(self._restart)
            custom_dates.addWidget(editor)
        search_card.content.addLayout(custom_dates)
        self.tag = QLineEdit()
        self.tag.setPlaceholderText('Filter by tag')
        self.tag.returnPressed.connect(self._restart)
        search_card.content.addWidget(self.tag)
        self.has_screenshot = QCheckBox('With screenshot')
        self.has_screenshot.toggled.connect(self._restart)
        search_card.content.addWidget(self.has_screenshot)
        self.has_structured_data = QCheckBox('With structured data')
        self.has_structured_data.toggled.connect(self._restart)
        search_card.content.addWidget(self.has_structured_data)
        search_button = AppButton('Search', variant='primary')
        search_button.clicked.connect(self._restart)
        search_card.content.addWidget(search_button)
        self.content.addWidget(search_card)

        results_card = Card()
        self.status = QLabel()
        self.status.setWordWrap(True)
        results_card.content.addWidget(self.status)
        self.results = QListWidget()
        self.results.setIconSize(QSize(80, 60))
        self.results.itemDoubleClicked.connect(self._open_item)
        results_card.content.addWidget(self.results)
        self.open_button = AppButton('Open', variant='secondary')
        self.open_button.clicked.connect(self._open_selected)
        results_card.content.addWidget(self.open_button)
        navigation = QHBoxLayout()
        self.previous_button = AppButton('Previous')
        self.next_button = AppButton('Next')
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        results_card.content.addLayout(navigation)
        self.content.addWidget(results_card)
        self.capture_button = AppButton('Capture Screenshot', variant='secondary')
        self.capture_button.clicked.connect(self.capture_requested)
        self.content.addWidget(self.capture_button)
        ask_card = Card()
        ask_card.content.addWidget(QLabel('Ask Memory'))
        self.ai_note = QLabel('AI Memory Questions are off. Enable them in Settings to send selected saved text to your configured AI provider. Screenshots are not sent.')
        self.ai_note.setWordWrap(True)
        ask_card.content.addWidget(self.ai_note)
        self.question = QLineEdit()
        self.question.setPlaceholderText('Ask about saved records...')
        self.question.returnPressed.connect(self._ask)
        ask_card.content.addWidget(self.question)
        self.ask_button = AppButton('Ask Memory')
        self.ask_button.clicked.connect(self._ask)
        ask_card.content.addWidget(self.ask_button)
        self.answer = QLabel()
        self.answer.setTextFormat(Qt.TextFormat.PlainText)
        self.answer.setWordWrap(True)
        ask_card.content.addWidget(self.answer)
        self.source_buttons = []
        self.source_layout = QVBoxLayout()
        ask_card.content.addLayout(self.source_layout)
        self.content.addWidget(ask_card)
        self.content.addStretch(1)
        self.set_ai_enabled(False)
        self._update_custom_dates()
        self.refresh()

    def set_ai_enabled(self, enabled):
        self.ask_button.setEnabled(enabled)
        self.question.setEnabled(enabled)
        self.ai_note.setText(
            'Ask Memory sends up to five retrieved saved text records to your configured AI provider. Original screenshots are not sent.'
            if enabled else
            'AI Memory Questions are off. Enable them in Settings to send selected saved text to your configured AI provider. Screenshots are not sent.')

    def _ask(self):
        question = self.question.text().strip()
        if self.ask_button.isEnabled() and question:
            self.ask_requested.emit(question, self._filters())

    def set_answer_pending(self):
        self.answer.setText('Searching saved memories and asking AI…')
        self.ask_button.setEnabled(False)

    def show_answer(self, answer, error=''):
        self.answer.setText(error or (answer.text if answer else 'Memory question failed.'))
        self.ask_button.setEnabled(self.question.isEnabled())
        for button in self.source_buttons:
            self.source_layout.removeWidget(button)
            button.deleteLater()
        self.source_buttons.clear()
        if answer and not error:
            for source in answer.sources:
                button = AppButton(f'[{source.reference}] {source.title}', variant='secondary')
                button.clicked.connect(lambda checked=False, memory_id=source.memory_id:
                                       self.memory_open_requested.emit(memory_id))
                self.source_layout.addWidget(button)
                self.source_buttons.append(button)

    def _restart(self, *_):
        self.offset = 0
        self._update_custom_dates()
        self.refresh()

    def _update_custom_dates(self):
        custom = self.date.currentData() == -1
        self.date_from.setVisible(custom)
        self.date_to.setVisible(custom)

    def _filters(self):
        filters = {}
        if self.source.currentData():
            filters['source_type'] = self.source.currentData()
        if self.tag.text().strip():
            filters['tag'] = self.tag.text().strip()
        if self.has_screenshot.isChecked():
            filters['has_screenshot'] = True
        if self.has_structured_data.isChecked():
            filters['has_structured_data'] = True
        days = self.date.currentData()
        if days == -1:
            start = datetime.combine(self.date_from.date().toPython(), time.min).astimezone()
            end = datetime.combine(self.date_to.date().toPython() + timedelta(days=1), time.min).astimezone()
            filters['saved_from'] = start.astimezone(timezone.utc).isoformat()
            filters['saved_to'] = end.astimezone(timezone.utc).isoformat()
        elif days:
            local_start = datetime.now().astimezone().replace(hour=0, minute=0,
                second=0, microsecond=0) - timedelta(days=days - 1)
            filters['saved_from'] = local_start.astimezone(timezone.utc).isoformat()
        return filters

    def refresh(self):
        try:
            hits = self.store.search(self.query.text(), self._filters(), limit=20, offset=self.offset)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        self.results.clear()
        for hit in hits:
            try:
                date = datetime.fromisoformat(hit.saved_at).astimezone().strftime('%Y-%m-%d %H:%M')
            except ValueError:
                date = hit.saved_at
            item = QListWidgetItem(f'{hit.title}\n{hit.snippet[:140]}\n{date}')
            record = self.store.get_record(hit.memory_id)
            thumbnail = self.store.thumbnail_path(record) if record else None
            if thumbnail:
                item.setIcon(QIcon(str(thumbnail)))
            item.setData(Qt.ItemDataRole.UserRole, hit.memory_id)
            self.results.addItem(item)
        if hits:
            self.status.setText(f'{self.offset + 1}–{self.offset + len(hits)} saved memories')
        elif not self.query.text().strip() and not self._filters():
            self.status.setText('No Visual Memories Yet. Save a screenshot result to begin.')
        else:
            self.status.setText('No matching memories found. Try clearing the filters.')
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(len(hits) == 20)
        self.open_button.setEnabled(bool(hits))

    def _open_item(self, item):
        self.memory_open_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _open_selected(self):
        item = self.results.currentItem() or self.results.item(0)
        if item:
            self._open_item(item)

    def next_page(self):
        if self.next_button.isEnabled():
            self.offset += 20
            self.refresh()

    def previous_page(self):
        if self.offset:
            self.offset = max(0, self.offset - 20)
            self.refresh()
