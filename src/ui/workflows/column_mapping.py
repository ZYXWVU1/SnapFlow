"""Ordered Sheet column to Skill field mapping control."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget


class ColumnMappingEditor(QWidget):
    changed = Signal()

    def __init__(self, fields, rows=(), parent=None):
        super().__init__(parent)
        self._fields = tuple(fields)
        self._rows = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.add_button = QPushButton('Add column')
        self.add_button.clicked.connect(lambda: self.add_row())
        self.layout.addWidget(self.add_button)
        self.set_rows(rows)

    def field_options(self):
        return list(self._fields)

    def add_row(self, column='', field=''):
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(column)
        name.setPlaceholderText('Sheet column')
        name.setAccessibleName('Sheet column name')
        choice = QComboBox()
        choice.setAccessibleName('Skill field')
        for key in self._fields:
            choice.addItem(key.replace('_', ' ').title(), key)
        choice.setCurrentIndex(max(0, choice.findData(field)))
        remove = QPushButton('Remove')
        remove.setAccessibleName('Remove column mapping')
        for widget in (name, choice, remove):
            line.addWidget(widget)
        self.layout.insertWidget(self.layout.count() - 1, row)
        self._rows.append((row, name, choice))
        name.textChanged.connect(self.changed)
        choice.currentIndexChanged.connect(self.changed)
        remove.clicked.connect(lambda: self.remove_row(row))
        self.changed.emit()

    def remove_row(self, row):
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        row.hide()
        row.deleteLater()
        self.changed.emit()

    def set_rows(self, rows):
        for row, _, _ in self._rows:
            row.hide()
            row.deleteLater()
        self._rows = []
        for item in rows:
            if isinstance(item, dict):
                self.add_row(item.get('column', ''), item.get('field', ''))
        self.changed.emit()

    def value(self):
        return [{'column': name.text().strip(), 'field': choice.currentData()}
                for _, name, choice in self._rows]
