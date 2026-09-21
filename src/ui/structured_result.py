"""Reusable, plain-text structured result presentation."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QScrollArea, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget)

from src.actions import display_value
from src.modes import ModeResult


class StructuredResult(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.section_titles = []
        self.table = None

    def render(self, result: ModeResult):
        previous = self.takeWidget()
        if previous:
            previous.deleteLater()
        body = QWidget()
        layout = QVBoxLayout(body)
        self.section_titles = []
        self.table = None

        def section(title, text, code=False):
            self.section_titles.append(title)
            heading = QLabel(title)
            heading.setTextFormat(Qt.TextFormat.PlainText)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            layout.addWidget(heading)
            label = QLabel(text or 'Not available')
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if code:
                label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            layout.addWidget(label)

        data = result.data
        if result.mode == 'debug':
            section('Detected Problem', data['error_type'] + '\n' + data['summary'])
            section('Language', data['language'])
            section('Evidence', '\n'.join(data['evidence']) or 'No visible evidence identified')
            section('Likely Cause', data['root_cause'])
            if not data['fixes']:
                section('Suggested Fix', 'No supported fix identified')
            for index, fix in enumerate(data['fixes'], 1):
                section(f'Suggested Fix {index} — Before', fix['before'], True)
                section('After', fix['after'], True)
                section('Why', fix['explanation'])
            section('Confidence', data['confidence'].title() + ' (model estimate)')
        else:
            kind = data['content_type']
            section('Extracted ' + kind.title(),
                    f"{len(data['rows'])} rows × {len(data['headers'])} columns" if kind == 'table' else 'Visible information from this screenshot')
            if kind == 'table':
                table = QTableWidget(len(data['rows']), len(data['headers']))
                table.setHorizontalHeaderLabels(data['headers'])
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                for i, row in enumerate(data['rows']):
                    for j, cell in enumerate(row):
                        table.setItem(i, j, QTableWidgetItem(display_value(cell)))
                table.setMinimumHeight(220)
                table.horizontalHeader().setStretchLastSection(True)
                layout.addWidget(table)
                self.table = table
            else:
                for key, value in data.items():
                    if key != 'content_type':
                        section(key.replace('_', ' ').title(), display_value(value) if value is not None else 'Not visible', key in ('code', 'value'))
        layout.addStretch()
        self.setWidget(body)
