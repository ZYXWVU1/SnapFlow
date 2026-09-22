"""Render validated skill data using each skill's presentation metadata."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QTableWidget,
                              QTableWidgetItem, QVBoxLayout, QWidget)
from src.skill_actions import readable
from src.skills.registry import SKILLS


def render_skill(view, result):
    previous = view.takeWidget()
    if previous:
        previous.deleteLater()
    body = QWidget()
    layout = QVBoxLayout(body)
    view.section_titles, view.table = [], None

    def section(title, value, code=False):
        if value is None or value == [] or value == '':
            return
        view.section_titles.append(title)
        heading = QLabel(title)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        label = QLabel(readable(value))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if code:
            label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(heading)
        layout.addWidget(label)

    skill = SKILLS[result.skill_id]
    section('Smart Result', result.title)
    for key, label in skill.presentation:
        value = result.data.get(key)
        if key == 'suggested_fixes':
            for i, fix in enumerate(value or [], 1):
                section(f'Suggested Fix {i}', fix.get('title') or fix.get('explanation'))
                section('Before', fix.get('before'), True)
                section('After', fix.get('after'), True)
                if fix.get('title'):
                    section('Why', fix.get('explanation'))
        else:
            section(label, value, key in ('error_message', 'evidence'))
    if skill.view == 'table':
        headers, rows = result.data['headers'], result.data['rows']
        section('Table preview', f'{len(rows)} rows × {len(headers)} columns\nShowing {min(10, len(rows))} of {len(rows)} rows')
        table = QTableWidget(min(10, len(rows)), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for i, row in enumerate(rows[:10]):
            for j, cell in enumerate(row):
                table.setItem(i, j, QTableWidgetItem(cell))
        table.setMinimumHeight(220)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        view.table = table
    section('Please review', result.warnings)
    if 'create_ics' in result.actions and (result.data.get('start_time') or result.data.get('due_time')) and not result.data.get('timezone'):
        section('Calendar timezone', 'No timezone was visible. The calendar file will use the importing calendar’s local time.')
    layout.addStretch()
    view.setWidget(body)
