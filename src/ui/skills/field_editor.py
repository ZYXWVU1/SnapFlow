from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QComboBox, QAbstractItemView
from src.skills.custom.models import CustomFieldDefinition, FIELD_TYPES, safe_id, unique_id


class FieldEditor(QWidget):
    changed = Signal()

    def __init__(self, fields=(), parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Field name', 'Type', 'Required', 'Description'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 155)
        self.table.setColumnWidth(1, 125)
        self.table.setColumnWidth(2, 70)
        self.table.setMinimumHeight(175)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for title, callback in [('+ Add Field', self.add_field), ('Delete Field', self.delete_field),
                                ('Move Up', lambda: self.move_field(-1)), ('Move Down', lambda: self.move_field(1))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        for field in fields:
            self.add_field(field)
        self.table.itemChanged.connect(self.changed)

    def add_field(self, field=None):
        if self.table.rowCount() >= 20:
            return
        if not isinstance(field, CustomFieldDefinition):
            field = None
        row = self.table.rowCount()
        self.table.insertRow(row)
        label = QTableWidgetItem(field.label if field else '')
        label.setData(Qt.ItemDataRole.UserRole, field.id if field else None)
        if field:
            label.setToolTip('Stored ID: ' + field.id)
        self.table.setItem(row, 0, label)
        kind = QComboBox()
        kind.addItems(FIELD_TYPES)
        kind.setCurrentText(field.field_type if field else 'string')
        kind.currentTextChanged.connect(self.changed)
        self.table.setCellWidget(row, 1, kind)
        required = QTableWidgetItem()
        required.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        required.setCheckState(Qt.CheckState.Checked if field and field.required else Qt.CheckState.Unchecked)
        self.table.setItem(row, 2, required)
        self.table.setItem(row, 3, QTableWidgetItem(field.description or '' if field else ''))
        self.table.selectRow(row)
        self.changed.emit()

    def delete_field(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def move_field(self, offset):
        row, target = self.table.currentRow(), self.table.currentRow() + offset
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        self.table.blockSignals(True)
        for col in (0, 2, 3):
            current, other = self.table.takeItem(row, col), self.table.takeItem(target, col)
            self.table.setItem(row, col, other)
            self.table.setItem(target, col, current)
        first, second = self.table.cellWidget(row, 1), self.table.cellWidget(target, 1)
        a, b = first.currentText(), second.currentText()
        first.setCurrentText(b)
        second.setCurrentText(a)
        self.table.blockSignals(False)
        self.table.selectRow(target)
        self.changed.emit()

    def definitions(self):
        result = []
        existing = {self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            stored_id = item.data(Qt.ItemDataRole.UserRole)
            field_id = stored_id or unique_id(safe_id(item.text()), existing)
            existing.add(field_id)
            result.append(CustomFieldDefinition(field_id, item.text().strip(), self.table.cellWidget(row, 1).currentText(),
                          self.table.item(row, 2).checkState() == Qt.CheckState.Checked, self.table.item(row, 3).text().strip()))
        return result
