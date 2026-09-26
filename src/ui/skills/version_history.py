"""Readable custom Skill history and controlled version operations."""
import difflib
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QTextEdit, QVBoxLayout

from src.ui.design.components import AppButton


def version_diff(left, right):
    lines = []
    for key, title in [('name', 'Name'), ('description', 'Description'),
                       ('detection_prompt', 'Detection Prompt'), ('extraction_prompt', 'Extraction Guidance'),
                       ('fields', 'Fields'), ('few_shot_examples', 'Examples'), ('actions', 'Actions')]:
        a, b = left.definition_snapshot.get(key), right.definition_snapshot.get(key)
        if a == b:
            continue
        lines.append(title + ':')
        if key == 'fields':
            a = [f"{f['id']} ({f['type']}): {f.get('description') or ''}" for f in a]
            b = [f"{f['id']} ({f['type']}): {f.get('description') or ''}" for f in b]
        elif key == 'few_shot_examples':
            a = [str(example) for example in a]
            b = [str(example) for example in b]
        elif isinstance(a, str):
            a, b = a.splitlines(), b.splitlines()
        else:
            a, b = list(a), list(b)
        lines.extend(difflib.unified_diff(a, b, fromfile=left.version_label,
                                          tofile=right.version_label, lineterm=''))
        lines.append('')
    return '\n'.join(lines) if lines else 'No definition changes.'


class VersionHistory(QDialog):
    def __init__(self, manager, skill_id, parent=None):
        super().__init__(parent)
        self.manager, self.skill_id = manager, skill_id
        self.setWindowTitle('Skill Version History')
        self.resize(700, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Version History · ' + skill_id.replace('_', ' ').title()))
        self.versions = QListWidget()
        self.versions.currentRowChanged.connect(self.show_comparison)
        layout.addWidget(self.versions)
        self.diff = QTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFontFamily('Consolas')
        layout.addWidget(self.diff, 2)
        row = QHBoxLayout()
        for label, callback in [('Publish Draft', self.publish_selected),
                                ('Restore Version', self.restore_selected),
                                ('Discard Draft', self.discard_selected), ('Close', self.close)]:
            button = AppButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        self.entries = self.manager.list_versions(self.skill_id)
        self.versions.clear()
        for version in self.entries:
            self.versions.addItem(f'{version.version_label} · {version.status.title()} · {version.created_at[:16].replace("T", " ")} · {version.description}')
        if self.entries:
            self.versions.setCurrentRow(len(self.entries) - 1)

    def selected(self):
        index = self.versions.currentRow()
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def show_comparison(self):
        selected = self.selected()
        current = next((version for version in reversed(self.entries) if version.status == 'published'), None)
        self.diff.setPlainText(version_diff(current, selected) if current and selected else '')

    def publish_selected(self):
        selected = self.selected()
        if selected is None or selected.status != 'draft':
            return
        if QMessageBox.question(self, 'Publish Skill', 'Publish this draft as the active Skill version?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.manager.publish(selected.version_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Cannot publish', str(exc))
        self.refresh()

    def restore_selected(self):
        selected = self.selected()
        if selected is None or selected.status == 'draft':
            return
        if QMessageBox.question(self, 'Restore Skill',
            'Create a new published revision from this historical version?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.manager.restore(selected.version_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Cannot restore', str(exc))
        self.refresh()

    def discard_selected(self):
        selected = self.selected()
        if selected and selected.status == 'draft':
            self.manager.delete_draft(selected.version_id)
            self.refresh()
