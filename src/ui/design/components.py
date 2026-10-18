"""Small native widgets shared by shell, pages, and result window."""
from PySide6.QtCore import QObject, QTimer, Qt, QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .tokens import SPACING


class AppButton(QPushButton):
    def __init__(self, label, *, variant='secondary', parent=None):
        super().__init__(label, parent)
        if variant not in ('primary', 'secondary', 'ghost', 'danger'):
            raise ValueError('Unknown button variant.')
        self.setProperty('variant', variant)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class ActionRow(QPushButton):
    """One native button with a generous title-and-description click target."""
    def __init__(self, title, description, icon_name, parent=None):
        super().__init__('', parent)
        from .icons import icon
        self.setProperty('role', 'command')
        self.setProperty('theme_icon', icon_name)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(88)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        row.addWidget(self.icon_label)
        text = QWidget()
        text.setProperty('role', 'command_content')
        column = QVBoxLayout(text)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.title_label = QLabel(title)
        title_font = self.title_label.font()
        title_font.setPointSize(12)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        column.addWidget(self.title_label)
        self.description_label = QLabel(description)
        self.description_label.setProperty('role', 'muted')
        self.description_label.setWordWrap(True)
        column.addWidget(self.description_label)
        row.addWidget(text, 1)
        arrow = QLabel('›')
        arrow.setProperty('role', 'muted')
        row.addWidget(arrow)
        for child in (self.icon_label, text, arrow):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setIcon(icon(icon_name))

    def setIcon(self, value):
        self.icon_label.setPixmap(value.pixmap(22, 22))

    def sizeHint(self):
        return self.layout().sizeHint().expandedTo(QSize(240, 88))

    def minimumSizeHint(self):
        return QSize(0, 88)

    def heightForWidth(self, width):
        return max(88, self.layout().totalHeightForWidth(width))


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
