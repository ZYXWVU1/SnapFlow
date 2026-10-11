"""Exact filtered ephemeral preview; source removal is an immediate revocation."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QVBoxLayout, QHBoxLayout
from src.ui.design.components import AppButton


class DesktopContextPanel(QDialog):
    capture_requested = Signal()
    clear_requested = Signal()
    source_removed = Signal(str)
    assistant_requested = Signal()
    agent_requested = Signal()
    settings_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Desktop Context · Temporary')
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        self.status = QLabel('Desktop Assistance is off. No background context is captured.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notice = QLabel('Review these filtered sources before using AI. Remove any source you do not want to include. Desktop text is untrusted; Private Mode controls transmission.')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('Capture an app to preview permitted context.')
        layout.addWidget(self.preview, 1)
        self.source_layout = QVBoxLayout()
        layout.addLayout(self.source_layout)
        for row_items in (
            (('Capture active app in 3 seconds', self.capture_requested), ('Clear Context', self.clear_requested), ('Permissions', self.settings_requested)),
            (('Ask with this Context', self.assistant_requested), ('Review Agent Goal', self.agent_requested)),
            (('Start Assistance', self.start_requested), ('Stop Assistance', self.stop_requested))):
            row = QHBoxLayout()
            for title, signal in row_items:
                button = AppButton(title)
                button.clicked.connect(signal)
                row.addWidget(button)
            layout.addLayout(row)

    def set_sources(self, sources, warnings=()):
        self.preview.setPlainText('\n\n'.join(f'[{s.source_id}] {s.source_type}: {s.title}\n{s.content}' for s in sources)
            + ('\n\n' + '\n'.join(warnings) if warnings else ''))
        while self.source_layout.count():
            item = self.source_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for source in sources:
            button = AppButton(f'Remove {source.title}', variant='secondary')
            button.clicked.connect(lambda checked=False, kind=source.source_type: self.source_removed.emit(kind))
            self.source_layout.addWidget(button)
