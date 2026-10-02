"""Common JSON Schema fields with a bounded JSON fallback for complex values."""
import json
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)
from src.mcp.client.discovery import bounded_json


class SchemaForm(QWidget):
    changed = Signal()

    def __init__(self, schema, values=None, parent=None, *, advanced=False):
        super().__init__(parent)
        self.inputs = {}
        self.advanced = None
        values = values or {}
        layout = QVBoxLayout(self)
        fields = schema.get('properties', {})
        unrepresentable = bool(set(values) - set(fields))
        for key, field in fields.items():
            if not isinstance(field, dict):
                unrepresentable = True
                continue
            value = values.get(key, field.get('default'))
            if len(field.get('enum', [])) > 128:
                unrepresentable = True
            if 'enum' in field and value is not None and value not in field['enum']:
                unrepresentable = True
            if field.get('type') in ('integer', 'number') and value is not None:
                if type(value) not in (int, float) or not -1000000000 <= value <= 1000000000:
                    unrepresentable = True
                elif field.get('type') == 'number' and round(value, 8) != value:
                    unrepresentable = True
            if field.get('type') == 'boolean' and value is not None and type(value) is not bool:
                unrepresentable = True
        if advanced or unrepresentable or not fields or any(key in schema for key in ('oneOf', 'anyOf', 'allOf', '$ref')) or len(fields) > 32:
            self.advanced = QPlainTextEdit(json.dumps(values, ensure_ascii=False, indent=2))
            self.advanced.setAccessibleName('Advanced JSON arguments')
            self.advanced.setMaximumHeight(180)
            self.advanced.textChanged.connect(self.changed)
            layout.addWidget(QLabel('Advanced JSON arguments (validated before execution)'))
            layout.addWidget(self.advanced)
            return
        form = QFormLayout()
        layout.addLayout(form)
        for key, field in fields.items():
            if not isinstance(field, dict):
                field = {}
            value = values.get(key, field.get('default'))
            required = key in schema.get('required', [])
            kind = field.get('type', 'string')
            if 'enum' in field:
                widget = QComboBox()
                for item in field['enum'][:128]:
                    widget.addItem(str(item), item)
                index = widget.findData(value)
                widget.setCurrentIndex(max(0, index))
                widget.currentIndexChanged.connect(self.changed)
            elif kind == 'boolean':
                widget = QCheckBox('True')
                widget.setChecked(value is True)
                widget.toggled.connect(self.changed)
            elif kind in ('integer', 'number'):
                widget = QSpinBox() if kind == 'integer' else QDoubleSpinBox()
                widget.setRange(-1000000000, 1000000000)
                if kind == 'number':
                    widget.setDecimals(8)
                widget.setValue(value if type(value) in (int, float) else 0)
                widget.valueChanged.connect(self.changed)
            elif kind in ('object', 'array') or any(k in field for k in ('$ref', 'oneOf', 'anyOf', 'allOf')):
                widget = QPlainTextEdit(json.dumps(value if value is not None else [] if kind == 'array' else {}, ensure_ascii=False))
                widget.setMaximumHeight(80)
                widget.textChanged.connect(self.changed)
            else:
                widget = QLineEdit(str(value) if value is not None else '')
                widget.setPlaceholderText('{skill_field} or literal text')
                widget.textChanged.connect(self.changed)
            widget.setAccessibleName(key)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            include = QCheckBox('Include')
            include.setChecked(required or key in values)
            include.setEnabled(not required)
            include.toggled.connect(self.changed)
            row.addWidget(include)
            row.addWidget(widget, 1)
            self.inputs[key] = (include, widget)
            label = QLabel(key + (' *' if required else ''))
            label.setTextFormat(Qt.TextFormat.PlainText)
            form.addRow(label, holder)

    def value(self):
        if self.advanced is not None:
            text = self.advanced.toPlainText()
            if len(text.encode('utf-8')) > 65536:
                raise ValueError('Arguments exceed 64 KiB.')
            result = json.loads(text)
        else:
            result = {}
            for key, (include, widget) in self.inputs.items():
                if not include.isChecked():
                    continue
                if isinstance(widget, QComboBox):
                    result[key] = widget.currentData()
                elif isinstance(widget, QCheckBox):
                    result[key] = widget.isChecked()
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    result[key] = widget.value()
                elif isinstance(widget, QPlainTextEdit):
                    text = widget.toPlainText()
                    if len(text.encode('utf-8')) > 65536:
                        raise ValueError('Arguments exceed 64 KiB.')
                    result[key] = json.loads(text)
                else:
                    result[key] = widget.text()
        if not isinstance(result, dict):
            raise ValueError('Arguments must be a JSON object.')
        bounded_json(result, 65536)
        return result
