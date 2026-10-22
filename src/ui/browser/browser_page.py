"""Readable page review and chat; setup is secondary to the user's task."""
import json
import re
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QDialog, QLabel, QPlainTextEdit, QVBoxLayout, QHBoxLayout,
    QMessageBox, QListWidget, QListWidgetItem, QLineEdit, QWidget, QSplitter, QToolButton, QComboBox, QScrollArea, QTabBar)
from src.agent.models import thaw
from src.ui.design.components import AppButton
from .ask_state import ask_state
from .source_preview import render_source, source_has_content


class BrowserCompanionDialog(QDialog):
    ask_requested = Signal(str)
    cancel_requested = Signal()
    source_changed = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._source_reference, self._busy = '', False
        self._displayed_source = None
        self._readable, self._fresh = False, True
        self._seen_snapshots = set()
        self._routing_mode = ''
        self.setWindowTitle('Browser Companion')
        self.setMinimumWidth(540)
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel('Browser Companion')
        font = title.font()
        font.setPointSize(17)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.status)
        self.stop_button = AppButton('Disconnect', variant='ghost')
        self.stop_button.clicked.connect(self.service.close)
        header.addWidget(self.stop_button)
        layout.addLayout(header)
        self.connections_label = QLabel()
        self.connections_label.setTextFormat(Qt.TextFormat.PlainText)
        self.connections_label.setWordWrap(True)
        layout.addWidget(self.connections_label)
        self.setup_toggle = QToolButton()
        self.setup_toggle.setText('Connection setup')
        self.setup_toggle.setCheckable(True)
        self.setup_toggle.setChecked(True)
        self.setup_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.setup_toggle)
        self.setup_content = QWidget()
        setup = QHBoxLayout(self.setup_content)
        setup.setContentsMargins(0, 0, 0, 0)
        column = QVBoxLayout()
        column.addWidget(QLabel('Approved extension IDs · one per line'))
        self.extension_ids = QPlainTextEdit()
        self.extension_ids.setAccessibleName('Approved browser extension IDs')
        self.extension_ids.setMaximumBlockCount(16)
        self.extension_ids.setMaximumHeight(64)
        column.addWidget(self.extension_ids)
        setup.addLayout(column, 1)
        self.enable_button = AppButton('Enable Browser Connection', variant='primary')
        self.enable_button.clicked.connect(self.enable)
        setup.addWidget(self.enable_button)
        layout.addWidget(self.setup_content)
        self.setup_toggle.toggled.connect(self.setup_content.setVisible)
        self.section_tabs = QTabBar()
        self.section_tabs.addTab('Ask')
        self.section_tabs.addTab('Reviewed source')
        self.section_tabs.setAccessibleName('Browser Companion section')
        self.section_tabs.currentChanged.connect(self._layout_sections)
        self.section_tabs.hide()
        layout.addWidget(self.section_tabs)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        review = QWidget()
        self.review_section = review
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(0, 0, 4, 0)
        review_layout.addWidget(QLabel('Authorized pages'))
        self.page_sessions = QListWidget()
        self.page_sessions.setMaximumHeight(72)
        self.page_sessions.currentItemChanged.connect(self.show_page)
        review_layout.addWidget(self.page_sessions)
        self.source_label = QLabel('No browser source selected.')
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setWordWrap(True)
        review_layout.addWidget(self.source_label)
        self.source_age = QLabel()
        self.source_age.setWordWrap(True)
        review_layout.addWidget(self.source_age)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('Browser context preview')
        self.preview.setPlaceholderText('Assist a page in the extension to review the text AI will use.')
        review_layout.addWidget(self.preview, 1)
        self.details_toggle = QToolButton()
        self.details_toggle.setText('Advanced source details')
        self.details_toggle.setCheckable(True)
        review_layout.addWidget(self.details_toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('Full filtered snapshot details')
        self.details.setMaximumHeight(100)
        self.details.hide()
        self.details_toggle.toggled.connect(self.details.setVisible)
        review_layout.addWidget(self.details)
        self.revoke_page_button = AppButton('Stop Selected Page', variant='danger')
        self.revoke_page_button.clicked.connect(self.revoke_page)
        review_layout.addWidget(self.revoke_page_button)
        self.splitter.addWidget(review)
        chat = QWidget()
        self.chat_section = chat
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(4, 0, 0, 0)
        chat_layout.addWidget(QLabel('Ask about this page'))
        self.answer = QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setAccessibleName('Answer about the reviewed page')
        self.answer.setPlaceholderText('Answers appear here with references to the reviewed page [B1].')
        chat_layout.addWidget(self.answer, 1)
        self.execution_label = QLabel()
        self.execution_label.setTextFormat(Qt.TextFormat.PlainText)
        self.execution_label.setWordWrap(True)
        self.execution_label.setAccessibleName('AI execution details')
        chat_layout.addWidget(self.execution_label)
        self.runtime_label = QLabel()
        self.runtime_label.setTextFormat(Qt.TextFormat.PlainText)
        self.runtime_label.setWordWrap(True)
        chat_layout.addWidget(self.runtime_label)
        self.memory_label = QLabel('Included Memory sources: None. Only the selected page is included.')
        self.memory_label.setWordWrap(True)
        chat_layout.addWidget(self.memory_label)
        self.route_choice = QComboBox()
        self.route_choice.setAccessibleName('AI routing choice')
        self.route_choice.currentIndexChanged.connect(self._update_ask)
        self.route_choice.hide()
        chat_layout.addWidget(self.route_choice)
        self.question = QLineEdit()
        self.question.setPlaceholderText('Enter your question about this page…')
        self.question.setMaxLength(500)
        self.question.setAccessibleName('Question about the reviewed browser page')
        self.question.textChanged.connect(self._update_ask)
        self.question.returnPressed.connect(self.ask)
        chat_layout.addWidget(self.question)
        self.ask_hint = QLabel()
        self.ask_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.ask_hint.setWordWrap(True)
        self.ask_hint.setAccessibleName('Ask readiness')
        chat_layout.addWidget(self.ask_hint)
        buttons = QHBoxLayout()
        self.ask_button = AppButton('Ask About Reviewed Page', variant='primary')
        self.ask_button.clicked.connect(self.ask)
        buttons.addWidget(self.ask_button, 1)
        self.cancel_button = AppButton('Cancel')
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        buttons.addWidget(self.cancel_button)
        chat_layout.addLayout(buttons)
        self.splitter.addWidget(chat)
        self.splitter.setSizes([430, 430])
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.body_scroll.setWidget(self.splitter)
        layout.addWidget(self.body_scroll, 1)
        footer = QHBoxLayout()
        help_text = QLabel('Refresh page context in the extension. SnapFlow can stay in the tray while you chat in your browser.')
        help_text.setWordWrap(True)
        footer.addWidget(help_text, 1)
        close = AppButton('Close', variant='ghost')
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.service.changed.connect(self.refresh)
        self.freshness_timer = QTimer(self)
        self.freshness_timer.setInterval(1000)
        self.freshness_timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.freshness_timer.start()

    def hideEvent(self, event):
        self.freshness_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_sections()

    def _layout_sections(self, *args):
        if not hasattr(self, 'chat_section'):
            return
        narrow = self.width() < 700
        self.section_tabs.setVisible(narrow)
        self.review_section.setVisible(not narrow or self.section_tabs.currentIndex() == 1)
        self.chat_section.setVisible(not narrow or self.section_tabs.currentIndex() == 0)

    def enable(self):
        try:
            ids = tuple(line.strip() for line in self.extension_ids.toPlainText().splitlines() if line.strip())
            if not 0 < len(ids) <= 16 or any(not re.fullmatch('[a-p]{32}', value) for value in ids):
                raise ValueError('Enter actual extension IDs.')
            self.service.enable(tuple('chrome-extension://' + value + '/' for value in ids))
            self.setup_toggle.setChecked(False)
        except (ValueError, PermissionError, OSError, ConnectionError, RuntimeError):
            QMessageBox.warning(self, 'Browser Companion',
                'Connection could not start. Check the extension IDs, Windows credential storage, '
                'Safe Mode and whether another SnapFlow instance is already connected.')
        self.refresh()

    def refresh(self):
        connections = self.service.connections()
        self.status.setText('Connected' if connections else 'Waiting for browser' if self.service.enabled else 'Stopped')
        self.stop_button.setEnabled(self.service.enabled)
        self.enable_button.setEnabled(not self.service.enabled)
        self.extension_ids.setEnabled(not self.service.enabled)
        self.connections_label.setText(' · '.join(c.browser_kind.title() for c in connections) or
            'Enable the connection, then click Connect to SnapFlow in your extension.')
        selected_id = self.selected_session()
        blocker = QSignalBlocker(self.page_sessions)
        self.page_sessions.clear()
        selected_item = None
        for session in self.service.sessions():
            item = QListWidgetItem(f'{session.page_title[:65]} · {session.origin}')
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.page_sessions.addItem(item)
            if session.session_id == selected_id:
                selected_item = item
        if selected_item:
            self.page_sessions.setCurrentItem(selected_item)
        elif self.page_sessions.count():
            self.page_sessions.setCurrentRow(0)
        del blocker
        self.show_page()

    def selected_session(self):
        item = self.page_sessions.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_source(self):
        if self._displayed_source is None:
            raise ValueError('Assist a page in the extension first.')
        return self._displayed_source

    def show_page(self, *args):
        reference, self._readable, self._fresh = '', False, True
        selected = self.selected_session()
        self._displayed_source = None
        self.revoke_page_button.setEnabled(selected is not None)
        try:
            if not self.service.context or not selected:
                raise ValueError('No selected source.')
            source = self.service.context.source(selected)
            self._displayed_source = source
            current = self.service.context.latest(selected)
            reference = source.resource_uri
            self._seen_snapshots.add(selected)
            self._readable = source_has_content(source.content)
            self.source_label.setText(f'[{source.source_id}] Reviewed page · {source.title}')
            self.preview.setPlainText(render_source(source.content))
            self.details.setPlainText('Source type: browser_dom\nExact bounded AI source:\n' + source.content +
                '\n\nFull filtered snapshot (AI receives only the bounded source above):\n' +
                json.dumps(thaw(current.data), indent=2, ensure_ascii=False))
            age = max(0, int(self.service.context.clock() - current.data['captured_at'] / 1000))
            warnings = ', '.join(current.data.get('warnings', ()))
            self.source_age.setText(f'Captured {age}s ago · expires after 2 minutes.' + (' Omitted content: ' + warnings if warnings else ''))
        except (ValueError, PermissionError, StopIteration):
            self._displayed_source = None
            self.preview.clear()
            self.details.clear()
            self._fresh = selected not in self._seen_snapshots
            self.source_age.setText('Refresh Page Context in the extension to capture a fresh source.' if selected else '')
            self.source_label.setText('No current page context.')
        if reference != self._source_reference:
            self._source_reference = reference
            self.answer.clear()
            self.execution_label.clear()
            self.source_changed.emit(reference)
            if reference and self.width() < 700:
                self.section_tabs.setCurrentIndex(1)
        self._update_ask()

    def set_routing(self, mode, private=False, fallback=False):
        signature = (mode, private, fallback)
        if signature != self._routing_mode:
            self._routing_mode = signature
            self.route_choice.clear()
            self.route_choice.addItem('Choose AI routing…', None)
            self.route_choice.addItem('Local only', 'local_only')
            if not private:
                self.route_choice.addItem('Cloud only', 'cloud_only')
        self.route_choice.setVisible(mode == 'ask_every_time' and not private)
        self._update_ask()

    def runtime_choice(self):
        return self.route_choice.currentData() if self.route_choice.isVisible() else None

    def revoke_page(self):
        if self.service.manager and self.selected_session():
            self.service.manager.revoke(self.selected_session())
            self.service.refresh()

    def _update_ask(self, *args):
        if not hasattr(self, 'ask_button'):
            return
        state = ask_state(connected=bool(self.service.connections()), authorized=self.selected_session() is not None,
            available=bool(self._source_reference), fresh=self._fresh, question=self.question.text(),
            busy=self._busy, readable=self._readable)
        needs_choice = self.route_choice.isVisible() and self.runtime_choice() is None
        self.ask_button.setEnabled(state.ready and not needs_choice)
        self.ask_hint.setText('Choose Local or Cloud before asking.' if state.ready and needs_choice else state.message)
        self.cancel_button.setEnabled(self._busy)
        self.question.setEnabled(not self._busy)

    def ask(self):
        self._update_ask()
        if self.ask_button.isEnabled():
            self.ask_requested.emit(self.question.text().strip())

    def set_busy(self, busy):
        self._busy = busy
        self._update_ask()
