"""Apply System, Light, or Dark appearance to the running application."""
import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFont, QFontDatabase

from .styles import stylesheet
from .tokens import DARK, LIGHT


class ThemeManager(QObject):
    def __init__(self, app, preference='system'):
        super().__init__(app)
        self.app = app
        self.preference = 'system'
        self.effective_theme = 'light'
        hints = app.styleHints()
        if hasattr(hints, 'colorSchemeChanged'):
            hints.colorSchemeChanged.connect(self._system_changed)
        self.set_preference(preference)

    def _system_theme(self):
        return 'dark' if self.app.styleHints().colorScheme() == Qt.ColorScheme.Dark else 'light'

    def _system_changed(self, _scheme):
        if self.preference == 'system':
            self.set_preference('system')

    def set_preference(self, preference):
        if preference not in ('system', 'light', 'dark'):
            raise ValueError('Unknown theme preference.')
        self.preference = preference
        self.effective_theme = self._system_theme() if preference == 'system' else preference
        families = QFontDatabase.families()
        if not families and os.name == 'nt':
            installed = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'segoeui.ttf'
            if installed.is_file():
                QFontDatabase.addApplicationFont(str(installed))
            families = QFontDatabase.families()
        font_name = next((name for name in ('Segoe UI Variable', 'Segoe UI') if name in families),
                         self.app.font().family())
        self.app.setFont(QFont(font_name, 10))
        self.app.setStyleSheet(stylesheet(DARK if self.effective_theme == 'dark' else LIGHT))
