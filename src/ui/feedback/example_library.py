"""Human-readable local verified-example browser."""
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                              QMessageBox, QScrollArea, QVBoxLayout, QWidget)

from src.ui.design.components import AppButton


def display_fields(data):
    return '\n'.join(f'{key.replace("_", " ").title()}: {value if value is not None else "Not visible"}'
                     for key, value in data.items()) or 'No fields'


class ExampleLibrary(QDialog):
    def __init__(self, storage, skill_id=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle('Verified Examples')
        self.resize(700, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Verified Examples'))
        self.skill_filter = QComboBox()
        self.skill_filter.addItem('All Skills', None)
        ids = sorted({item.skill_id for item in storage.list_examples()})
        for item_id in ids:
            self.skill_filter.addItem(item_id.replace('_', ' ').title(), item_id)
        if skill_id in ids:
            self.skill_filter.setCurrentIndex(self.skill_filter.findData(skill_id))
        self.skill_filter.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.skill_filter)
        self.examples = QListWidget()
        self.examples.currentRowChanged.connect(self.show_selected)
        layout.addWidget(self.examples, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.meta = QLabel()
        self.original = QLabel()
        self.corrected = QLabel()
        self.changed = QLabel()
        self.image_notice = QLabel()
        self.image = QLabel()
        for widget in (self.meta, self.original, self.corrected, self.changed, self.image_notice, self.image):
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setWordWrap(True)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail_layout.addWidget(widget)
        scroll.setWidget(detail)
        layout.addWidget(scroll, 2)
        row = QHBoxLayout()
        for label, callback in [('Delete Example', self.delete_selected),
                                ('Clear Saved Feedback', self.clear_all), ('Close', self.close)]:
            button = AppButton(label, variant='danger' if label != 'Close' else 'ghost')
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.records = []
        self.refresh()

    def refresh(self):
        self.records = self.storage.list_examples(self.skill_filter.currentData())
        self.examples.clear()
        for record in self.records:
            self.examples.addItem(f'{record.created_at[:16].replace("T", " ")} · {record.skill_id.replace("_", " ").title()} · {record.status.title()}')
        self.show_selected(self.examples.currentRow())

    def show_selected(self, index):
        record = self.records[index] if 0 <= index < len(self.records) else None
        self.meta.setText(f'Skill: {record.skill_id} · Status: {record.status} · Created: {record.created_at}' if record else 'Select an example.')
        self.original.setText('Original AI result\n' + display_fields(record.original_result) if record else '')
        self.corrected.setText('Verified result\n' + display_fields(record.corrected_result) if record else '')
        self.changed.setText('Changed fields: ' + ', '.join(record.changed_fields) if record else '')
        self.image.clear()
        path = Path(record.screenshot_reference) if record and record.screenshot_reference else None
        if path and path.is_file():
            pixmap = QPixmap(str(path))
            self.image.setPixmap(pixmap.scaled(520, 280, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
            self.image_notice.setText('Saved screenshot')
        else:
            self.image_notice.setText('No screenshot saved.' if record else '')

    def delete_selected(self):
        index = self.examples.currentRow()
        if index < 0:
            return
        if QMessageBox.question(self, 'Delete example', 'Delete this verified example and its saved screenshot?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.storage.delete(self.records[index].id)
            except (OSError, sqlite3.Error) as exc:
                QMessageBox.warning(self, 'Unable to delete example', str(exc))
                return
            self.refresh()

    def clear_all(self):
        if QMessageBox.question(self, 'Clear feedback', 'Delete all saved examples and screenshots?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.storage.clear()
            except (OSError, sqlite3.Error) as exc:
                QMessageBox.warning(self, 'Unable to clear feedback', str(exc))
                return
            self.refresh()
