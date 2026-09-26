"""Resource chooser that stores the provider ID rather than the display name."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from src.ui.design.components import AppButton


class ResourcePicker(QWidget):
    changed = Signal()
    load_requested = Signal()

    def __init__(self, value='', parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setAccessibleName('Integration resource')
        self.combo.setEditText(value or '')
        self.load_button = AppButton('Choose…')
        self.load_button.setToolTip('Load resources from the connected service')
        row.addWidget(self.combo, 1)
        row.addWidget(self.load_button)
        self.combo.currentIndexChanged.connect(self.changed)
        self.combo.editTextChanged.connect(self.changed)
        self.load_button.clicked.connect(self.load_requested)

    def value(self):
        index = self.combo.currentIndex()
        if index >= 0 and self.combo.currentText() == self.combo.itemText(index):
            return self.combo.currentData() or self.combo.currentText().strip()
        return self.combo.currentText().strip()

    def setText(self, value):
        self.combo.setCurrentIndex(-1)
        self.combo.setEditText(value)

    def set_options(self, options):
        previous = self.value()
        self.combo.blockSignals(True)
        self.combo.clear()
        for label, resource_id in options:
            self.combo.addItem(label, resource_id)
        index = self.combo.findData(previous)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        else:
            self.combo.setCurrentIndex(-1)
            self.combo.setEditText(previous)
        self.combo.blockSignals(False)
        self.changed.emit()

    def set_busy(self, busy):
        self.load_button.setEnabled(not busy)
        self.load_button.setText('Loading…' if busy else 'Choose…')
