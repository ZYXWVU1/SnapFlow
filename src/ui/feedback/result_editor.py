"""Schema-driven editor for a reviewed extraction."""
from copy import deepcopy
from PySide6.QtCore import Qt, QDate, QTime, QDateTime
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
                              QCheckBox, QDateEdit, QTimeEdit, QDateTimeEdit)


class TemporalField(QWidget):
    def __init__(self, kind, value=None):
        super().__init__()
        self.kind = kind
        self.original = None
        self.dirty = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.missing = QCheckBox('Not visible')
        if kind == 'date':
            self.input = QDateEdit()
            self.input.setCalendarPopup(True)
            self.input.setDisplayFormat('yyyy-MM-dd')
            self.input.dateChanged.connect(self._mark_dirty)
        elif kind == 'time':
            self.input = QTimeEdit()
            self.input.setDisplayFormat('HH:mm')
            self.input.timeChanged.connect(self._mark_dirty)
        else:
            self.input = QDateTimeEdit()
            self.input.setCalendarPopup(True)
            self.input.setDisplayFormat('yyyy-MM-dd HH:mm')
            self.input.dateTimeChanged.connect(self._mark_dirty)
        self.missing.toggled.connect(lambda checked: self.input.setEnabled(not checked))
        row.addWidget(self.input, 1)
        row.addWidget(self.missing)
        self.set_value(value)

    def _mark_dirty(self, *_):
        self.dirty = True

    def set_value(self, value):
        self.original = value
        self.input.blockSignals(True)
        if self.kind == 'date':
            parsed = QDate.fromString(value, 'yyyy-MM-dd') if isinstance(value, str) else QDate()
            self.input.setDate(parsed if parsed.isValid() else QDate.currentDate())
        elif self.kind == 'time':
            parsed = QTime.fromString(value, 'HH:mm') if isinstance(value, str) else QTime()
            self.input.setTime(parsed if parsed.isValid() else QTime.currentTime())
        else:
            parsed = QDateTime.fromString(value, Qt.DateFormat.ISODate) if isinstance(value, str) else QDateTime()
            self.input.setDateTime(parsed if parsed.isValid() else QDateTime.currentDateTime())
        self.input.blockSignals(False)
        self.missing.setChecked(value is None)
        self.input.setEnabled(value is not None)
        self.dirty = False

    def value(self):
        if self.missing.isChecked():
            return None
        if self.original is not None and not self.dirty:
            return self.original
        if self.kind == 'date':
            return self.input.date().toString('yyyy-MM-dd')
        if self.kind == 'time':
            return self.input.time().toString('HH:mm')
        return self.input.dateTime().toString(Qt.DateFormat.ISODate)


class ResultEditor(QDialog):
    def __init__(self, extraction, definition, parent=None):
        super().__init__(parent)
        self.extraction, self.definition = extraction, definition
        self.widgets = {}
        self.setWindowTitle('Edit Extracted Information')
        self.resize(520, 560)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        for field_def in definition.fields:
            value = extraction.edited_data.get(field_def.id)
            if field_def.field_type == 'boolean':
                widget = QComboBox()
                for label, state in [('Not visible', None), ('Yes', True), ('No', False)]:
                    widget.addItem(label, state)
                widget.setCurrentIndex(widget.findData(value))
            elif field_def.field_type in ('multiline_text', 'list_string'):
                widget = QTextEdit()
                widget.setMaximumHeight(96)
                widget.setPlainText('\n'.join(value) if isinstance(value, list) else value or '')
            elif field_def.field_type in ('date', 'time', 'datetime'):
                widget = TemporalField(field_def.field_type, value)
            else:
                widget = QLineEdit()
                widget.setText('' if value is None else str(value))
                widget.setPlaceholderText({
                    'number': 'Number', 'date': 'YYYY-MM-DD', 'time': 'HH:MM',
                    'datetime': 'YYYY-MM-DDTHH:MM', 'url': 'https://example.com',
                    'email': 'name@example.com',
                }.get(field_def.field_type, 'Not visible'))
            widget.setAccessibleName(field_def.label)
            if field_def.description:
                widget.setToolTip(field_def.description)
            self.widgets[field_def.id] = widget
            form.addRow(field_def.label + (' *' if field_def.required else ''), widget)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        row = QHBoxLayout()
        for label, callback in [('Reset', self.reset), ('Cancel', self.reject), ('Save Corrections', self.save)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        outer.addLayout(row)

    def values(self):
        values = {}
        for field_def in self.definition.fields:
            widget = self.widgets[field_def.id]
            if isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, TemporalField):
                value = widget.value()
            elif isinstance(widget, QTextEdit):
                text = widget.toPlainText()
                value = [line.strip() for line in text.splitlines() if line.strip()] if field_def.field_type == 'list_string' else text
            else:
                value = widget.text().strip()
                if field_def.field_type == 'number' and value:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
            values[field_def.id] = value
        return values

    def reset(self):
        for field_def in self.definition.fields:
            value = self.extraction.original_data.get(field_def.id)
            widget = self.widgets[field_def.id]
            if isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(value))
            elif isinstance(widget, TemporalField):
                widget.set_value(value)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText('\n'.join(value) if isinstance(value, list) else value or '')
            else:
                widget.setText('' if value is None else str(value))
        self.error.clear()

    def save(self):
        try:
            draft = deepcopy(self.extraction)
            draft.apply(self.values(), self.definition)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.extraction.edited_data = draft.edited_data
        self.extraction.changed_fields = draft.changed_fields
        self.extraction.updated_at = draft.updated_at
        self.accept()
