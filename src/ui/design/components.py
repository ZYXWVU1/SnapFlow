"""Small native widgets shared by shell, pages, and result window."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .tokens import SPACING


class AppButton(QPushButton):
    def __init__(self, label, *, variant='secondary', parent=None):
        super().__init__(label, parent)
        if variant not in ('primary', 'secondary', 'ghost', 'danger'):
            raise ValueError('Unknown button variant.')
        self.setProperty('variant', variant)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'card')
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(SPACING['xl'], SPACING['xl'], SPACING['xl'], SPACING['xl'])
        self.content.setSpacing(SPACING['md'])


class Badge(QLabel):
    def __init__(self, label, *, status='neutral', parent=None):
        super().__init__(label, parent)
        if status not in ('neutral', 'success', 'warning', 'error'):
            raise ValueError('Unknown badge status.')
        self.status = status
        self.setProperty('role', 'badge')
        self.setProperty('status', status)
        self.setAccessibleName(label)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class PageHeader(QWidget):
    def __init__(self, title, subtitle='', action=None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(SPACING['xs'])
        self.title_label = QLabel(title)
        font = self.title_label.font()
        font.setPointSize(20)
        font.setWeight(font.Weight.DemiBold)
        self.title_label.setFont(font)
        text.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty('role', 'muted')
        self.subtitle_label.setWordWrap(True)
        text.addWidget(self.subtitle_label)
        row.addLayout(text, 1)
        if action is not None:
            action.setParent(self)
            row.addWidget(action)


class ToastManager(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.label = QLabel(parent)
        self.label.setProperty('role', 'toast')
        self.label.setAccessibleName('Status message')
        self.label.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.label.hide)

    def show_message(self, message, *, duration_ms=3500):
        self.timer.stop()
        self.label.setText(message)
        self.label.adjustSize()
        parent = self.label.parentWidget()
        self.label.move(max(SPACING['lg'], parent.width() - self.label.width() - SPACING['lg']), SPACING['lg'])
        self.label.raise_()
        self.label.show()
        self.timer.start(duration_ms)
