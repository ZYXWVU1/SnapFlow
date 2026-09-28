"""Release notes shown before a user opens the official download page."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from src.app_version import APP_NAME, APP_VERSION
from src.release_config import RELEASE_PAGE_URL


class UpdateDialog(QDialog):
    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{APP_NAME} update available')
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        version = QLabel(f'{APP_NAME} {APP_VERSION} → {release.tag_name} is available')
        version.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(version)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.notes or 'No release notes were provided.')
        layout.addWidget(notes, 1)
        disclosure = QLabel('SnapFlow will open the configured GitHub Releases page in your browser. It will not download or run files.')
        disclosure.setWordWrap(True)
        disclosure.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(disclosure)
        buttons = QHBoxLayout()
        later = QPushButton('Later')
        open_page = QPushButton('Download Update')
        open_page.setDefault(True)
        later.clicked.connect(self.reject)
        open_page.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(later)
        buttons.addWidget(open_page)
        layout.addLayout(buttons)


def official_release_page():
    """Return the fixed, trusted release page; response URLs are never used."""
    return RELEASE_PAGE_URL
