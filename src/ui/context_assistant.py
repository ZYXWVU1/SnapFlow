"""Session-scoped native Qt view for contextual screenshot questions."""
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QBoxLayout, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QScrollArea, QTextBrowser, QVBoxLayout, QWidget)

from src.ui.design.components import AppButton, Card, PageHeader


class ContextAssistantDialog(QDialog):
    question_requested = Signal(str, str, object)
    scope_changed = Signal(str)
    permission_revoked = Signal()
    clear_requested = Signal()
    memory_open_requested = Signal(str)
    source_removed = Signal(str)
    current_source_requested = Signal()
    suggestion_requested = Signal(str)
    cancel_requested = Signal()
    closed = Signal()

    def __init__(self, store, parent=None, *, memory_enabled=True, memory_search_enabled=True):
        super().__init__(parent)
        self.store = store
        self.selected = set()
        self._busy = False
        self.source_buttons = []
        self.source_remove_buttons = []
        self.suggestion_buttons = []
        self._citation_targets = {}
        self.setWindowTitle('Contextual Assistant')
        self.resize(740, 690)
        self.setMinimumSize(420, 430)
        outer = QVBoxLayout(self)
        outer.addWidget(PageHeader('Contextual Assistant',
            'Compare the current screenshot analysis with memories you authorize.'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.panels_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, content)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.panels_layout.addWidget(left_panel, 1)
        self.panels_layout.addWidget(right_panel, 1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        scope_card = Card()
        scope_card.content.addWidget(QLabel('Context Sources'))
        self.scope = QComboBox()
        self.scope.addItem('Current screenshot only', 'current_only')
        if memory_enabled:
            self.scope.addItem('Current + selected memories', 'selected_memories')
            if memory_search_enabled:
                self.scope.addItem('Search my Visual Memory', 'authorized_memory_search')
        self.scope.currentIndexChanged.connect(self._scope_changed)
        scope_card.content.addWidget(self.scope)
        self.scope_note = QLabel()
        self.scope_note.setWordWrap(True)
        self.scope_note.setTextFormat(Qt.TextFormat.PlainText)
        scope_card.content.addWidget(self.scope_note)
        left_layout.addWidget(scope_card)

        self.memory_card = Card()
        self.memory_query = QLineEdit()
        self.memory_query.setPlaceholderText('Find a saved memory to include...')
        self.memory_query.returnPressed.connect(self.refresh_memories)
        self.memory_card.content.addWidget(self.memory_query)
        search = AppButton('Find Memories')
        search.clicked.connect(self.refresh_memories)
        self.memory_card.content.addWidget(search)
        self.memory_list = QListWidget()
        self.memory_list.setMinimumHeight(110)
        self.memory_list.setMaximumHeight(120)
        self.memory_list.itemChanged.connect(self._item_changed)
        self.memory_card.content.addWidget(self.memory_list)
        self.selected_summary = QLabel()
        self.selected_summary.setWordWrap(True)
        self.selected_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.memory_card.content.addWidget(self.selected_summary)
        left_layout.addWidget(self.memory_card)
        left_layout.addStretch()

        answer_card = Card()
        self.answer = QTextBrowser()
        self.answer.setReadOnly(True)
        self.answer.setOpenLinks(False)
        self.answer.setOpenExternalLinks(False)
        self.answer.anchorClicked.connect(self._open_citation)
        self.answer.setMinimumHeight(150)
        self.answer.setPlaceholderText('Answers and citations appear here.')
        answer_card.content.addWidget(self.answer)
        self.source_layout = QVBoxLayout()
        answer_card.content.addLayout(self.source_layout)
        right_layout.addWidget(answer_card, 1)
        self.suggestion_card = Card()
        self.suggestion_card.content.addWidget(QLabel('Suggested Next Steps'))
        self.suggestion_layout = QVBoxLayout()
        self.suggestion_card.content.addLayout(self.suggestion_layout)
        self.suggestion_card.hide()
        right_layout.addWidget(self.suggestion_card)

        self.question = QLineEdit()
        self.question.setPlaceholderText('Ask about this screenshot...')
        self.question.returnPressed.connect(self._ask)
        outer.addWidget(self.question)
        controls = QHBoxLayout()
        self.ask_button = AppButton('Ask with Context', variant='primary')
        self.ask_button.clicked.connect(self._ask)
        self.cancel_button = AppButton('Cancel Request', variant='secondary')
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        self.revoke_button = AppButton('Revoke Memory Access', variant='secondary')
        self.revoke_button.clicked.connect(self._revoke)
        clear = AppButton('Clear Session', variant='secondary')
        clear.clicked.connect(self._clear)
        close = AppButton('Close', variant='ghost')
        close.clicked.connect(self.close)
        for button in (self.ask_button, self.cancel_button, self.revoke_button):
            controls.addWidget(button)
        outer.addLayout(controls)
        secondary_controls = QHBoxLayout()
        for button in (clear, close):
            secondary_controls.addWidget(button)
        outer.addLayout(secondary_controls)
        self._scope_changed()

    def resizeEvent(self, event):
        if hasattr(self, 'panels_layout'):
            direction = (QBoxLayout.Direction.TopToBottom if event.size().width() < 650
                         else QBoxLayout.Direction.LeftToRight)
            self.panels_layout.setDirection(direction)
        super().resizeEvent(event)

    def _scope_changed(self, *_):
        scope = self.scope.currentData()
        self.memory_card.setVisible(scope == 'selected_memories')
        if scope == 'selected_memories' and not self.memory_list.count():
            self.refresh_memories()
        self.revoke_button.setVisible(scope != 'current_only')
        notes = {
            'current_only': 'Only the current screenshot analysis will be sent to your configured AI provider.',
            'selected_memories': 'Checked saved text records will be sent with the current analysis when you press Ask. Original saved screenshots are excluded.',
            'authorized_memory_search': 'Relevant saved text may be searched and sent to your configured AI provider when you press Ask. Original saved screenshots are excluded.',
        }
        self.scope_note.setText(notes[scope])
        self.scope_changed.emit(scope)

    def refresh_memories(self):
        try:
            hits = self.store.search(self.memory_query.text(), limit=20)
            retained = []
            for memory_id in sorted(self.selected):
                record = self.store.get_record(memory_id)
                if record is not None:
                    retained.append(record)
                else:
                    self.selected.discard(memory_id)
        except (OSError, ValueError) as exc:
            self.scope_note.setText(f'Unable to search saved memories: {exc}')
            return
        self.memory_list.blockSignals(True)
        self.memory_list.clear()
        shown = set()
        for hit in (*retained, *hits):
            memory_id = getattr(hit, 'memory_id', getattr(hit, 'id', None))
            if memory_id in shown:
                continue
            shown.add(memory_id)
            item = QListWidgetItem(f'{hit.title} · {hit.saved_at[:10]}')
            item.setData(Qt.ItemDataRole.UserRole, memory_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if memory_id in self.selected
                               else Qt.CheckState.Unchecked)
            self.memory_list.addItem(item)
        self.memory_list.blockSignals(False)
        self._update_selected_summary()

    def _item_changed(self, item):
        memory_id = item.data(Qt.ItemDataRole.UserRole)
        if memory_id:
            if item.checkState() == Qt.CheckState.Checked:
                if len(self.selected) >= 8 and memory_id not in self.selected:
                    item.setCheckState(Qt.CheckState.Unchecked)
                    self.scope_note.setText('Select at most eight saved memories.')
                    return
                self.selected.add(memory_id)
            else:
                self.selected.discard(memory_id)
            self._update_selected_summary()

    def _update_selected_summary(self):
        names = [self.memory_list.item(index).text().split(' · ')[0]
            for index in range(self.memory_list.count())
            if self.memory_list.item(index).data(Qt.ItemDataRole.UserRole) in self.selected]
        self.selected_summary.setText(f'Selected memories ({len(self.selected)}): '
            + (', '.join(names) if names else 'none'))

    def _ask(self):
        question = self.question.text().strip()
        scope = self.scope.currentData()
        if not question or not self.ask_button.isEnabled():
            return
        ids = tuple(sorted(self.selected)) if scope == 'selected_memories' else ()
        if scope == 'selected_memories' and not ids:
            self.scope_note.setText('Check at least one saved memory before asking.')
            return
        self.question_requested.emit(question, scope, ids)

    def set_busy(self, question=''):
        self._busy = True
        self.ask_button.setEnabled(False)
        self.scope.setEnabled(False)
        self.memory_list.setEnabled(False)
        self.memory_query.setEnabled(False)
        self.cancel_button.show()
        if question:
            self._append_answer('User: ' + question)
        self._append_answer('Searching authorized sources and preparing an answer…')

    def show_result(self, result):
        self._busy = False
        self.ask_button.setEnabled(True)
        self.scope.setEnabled(True)
        self.memory_list.setEnabled(True)
        self.memory_query.setEnabled(True)
        self.cancel_button.hide()
        for warning in result.warnings:
            self._append_answer(warning)
        self._append_answer('Assistant: ' + result.answer, result=result)
        self.question.clear()
        self._clear_source_buttons()
        for source in result.sources:
            button = AppButton(f'[{source.source_id}] {source.title}', variant='secondary')
            if source.memory_id:
                button.clicked.connect(lambda checked=False, memory_id=source.memory_id:
                                       self.memory_open_requested.emit(memory_id))
            else:
                button.clicked.connect(self.current_source_requested)
            self.source_layout.addWidget(button)
            self.source_buttons.append(button)
            if source.memory_id:
                remove = AppButton(f'Remove {source.title} from future context', variant='ghost')
                remove.clicked.connect(lambda checked=False, memory_id=source.memory_id,
                    control=remove: self._remove_source(memory_id, control))
                self.source_layout.addWidget(remove)
                self.source_remove_buttons.append(remove)

    def _remove_source(self, memory_id, control):
        self.selected.discard(memory_id)
        for index in range(self.memory_list.count()):
            item = self.memory_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == memory_id:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._update_selected_summary()
        control.setEnabled(False)
        self._append_answer('Saved source removed from future context.')
        self.source_removed.emit(memory_id)

    def show_error(self, message):
        self._busy = False
        self.ask_button.setEnabled(True)
        self.scope.setEnabled(True)
        self.memory_list.setEnabled(True)
        self.memory_query.setEnabled(True)
        self.cancel_button.hide()
        self._append_answer(message)

    def _cancel(self):
        if not self._busy:
            return
        self._busy = False
        self.ask_button.setEnabled(True)
        self.scope.setEnabled(True)
        self.memory_list.setEnabled(True)
        self.memory_query.setEnabled(True)
        self.cancel_button.hide()
        self._append_answer('Request cancelled.')
        self.cancel_requested.emit()

    def show_suggestions(self, suggestions):
        self._clear_suggestions()
        for suggestion in suggestions:
            button = AppButton(suggestion.label, variant='secondary')
            button.setToolTip(suggestion.description)
            button.clicked.connect(lambda checked=False, suggestion_id=suggestion.suggestion_id:
                                   self.suggestion_requested.emit(suggestion_id))
            self.suggestion_layout.addWidget(button)
            self.suggestion_buttons.append(button)
        self.suggestion_card.setVisible(bool(self.suggestion_buttons))

    def _clear_suggestions(self):
        for button in self.suggestion_buttons:
            self.suggestion_layout.removeWidget(button)
            button.deleteLater()
        self.suggestion_buttons.clear()
        self.suggestion_card.hide()

    def _clear_source_buttons(self):
        for button in (*self.source_buttons, *self.source_remove_buttons):
            self.source_layout.removeWidget(button)
            button.deleteLater()
        self.source_buttons.clear()
        self.source_remove_buttons.clear()

    def _append_answer(self, value, *, result=None):
        cursor = self.answer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.answer.document().isEmpty():
            cursor.insertBlock()
        normal = QTextCharFormat()
        sources = {source.source_id: source for source in result.sources} if result else {}
        for part in re.split(r'(\[[A-Za-z]\d+\])', value):
            source = sources.get(part[1:-1]) if part.startswith('[') else None
            if source is None:
                cursor.insertText(part, normal)
                continue
            href = f'context-source:{result.request_id}:{source.source_id}'
            self._citation_targets[href] = source.memory_id
            linked = QTextCharFormat()
            linked.setAnchor(True)
            linked.setAnchorHref(href)
            linked.setFontUnderline(True)
            cursor.insertText(part, linked)
        self.answer.setTextCursor(cursor)

    def _open_citation(self, url):
        href = url.toString()
        if href not in self._citation_targets:
            return
        memory_id = self._citation_targets[href]
        if memory_id is None:
            self.current_source_requested.emit()
        else:
            self.memory_open_requested.emit(memory_id)

    def _revoke(self):
        self._cancel()
        self.scope.setCurrentIndex(self.scope.findData('current_only'))
        self.ask_button.setEnabled(True)
        self.selected.clear()
        self._clear_suggestions()
        self.memory_list.clear()
        self._update_selected_summary()
        self._append_answer('Memory access revoked for future questions. Content already sent to the provider cannot be recalled.')
        self.permission_revoked.emit()

    def _clear(self):
        self.answer.clear()
        self._citation_targets.clear()
        self._clear_source_buttons()
        self._clear_suggestions()
        self._revoke()
        self.clear_requested.emit()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
