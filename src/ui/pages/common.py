"""Shared scrollable page and compact metadata card."""
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from src.ui.design.components import Card
from src.ui.design.tokens import SPACING


class ScrollPage(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        body = QWidget()
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(SPACING['2xl'], SPACING['2xl'], SPACING['2xl'], SPACING['2xl'])
        self.content.setSpacing(SPACING['xl'])
        self.setWidget(body)


class NamedCard(Card):
    def __init__(self, name, description='', parent=None):
        super().__init__(parent)
        self.name = name
        title = QLabel(name)
        font = title.font()
        font.setPointSize(12)
        font.setBold(True)
        title.setFont(font)
        title.setWordWrap(True)
        self.content.addWidget(title)
        if description:
            detail = QLabel(description)
            detail.setProperty('role', 'muted')
            detail.setWordWrap(True)
            self.content.addWidget(detail)
