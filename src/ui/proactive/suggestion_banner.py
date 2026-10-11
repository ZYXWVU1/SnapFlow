from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout
from src.ui.design.components import AppButton


class SuggestionBanner(QWidget):
    accepted = Signal(str)
    dismissed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label, 1)
        review = AppButton('Review Agent Goal')
        review.clicked.connect(lambda: self.accepted.emit(self.suggestion_id))
        layout.addWidget(review)
        dismiss = AppButton('Dismiss', variant='ghost')
        dismiss.clicked.connect(lambda: self.dismissed.emit(self.suggestion_id))
        layout.addWidget(dismiss)
        self.suggestion_id = ''
        self.hide()

    def show_suggestion(self, suggestion):
        self.suggestion_id = suggestion.suggestion_id
        self.label.setText(suggestion.title + '\n' + suggestion.description)
        self.show()  # Only an in-window banner. Never activate/raise another window.
