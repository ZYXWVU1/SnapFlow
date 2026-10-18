"""Dismissible background for the compact navigation pane."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame


class NavigationScrim(QFrame):
    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'navigation_scrim')
        self.setAccessibleName('Navigation backdrop')
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().x() >= 196:
            self.dismissed.emit()
        event.accept()
