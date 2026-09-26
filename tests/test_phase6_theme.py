import unittest

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from src.ui.design.components import AppButton, Badge, Card, PageHeader, ToastManager
from src.ui.design.tokens import DARK, LIGHT, SPACING
from src.ui.design.theme import ThemeManager
from src.config import Config
from src.ui.settings_window import SettingsWindow


APP = QApplication.instance() or QApplication([])


class Phase6ThemeTests(unittest.TestCase):
    def test_palettes_expose_semantic_roles(self):
        roles = ('background', 'surface', 'surface_alt', 'border', 'text_primary',
                 'text_secondary', 'accent', 'accent_hover', 'success', 'warning',
                 'error', 'focus')
        for palette in (LIGHT, DARK):
            for role in roles:
                self.assertTrue(getattr(palette, role).startswith('#'))
        self.assertNotEqual(LIGHT.background, DARK.background)
        self.assertEqual(SPACING['lg'], 16)

    def test_theme_switch_updates_application(self):
        previous = APP.styleSheet()
        manager = ThemeManager(APP, 'light')
        try:
            self.assertTrue(QFontDatabase.families())
            self.assertEqual(manager.effective_theme, 'light')
            self.assertIn(LIGHT.background, APP.styleSheet())
            manager.set_preference('dark')
            self.assertEqual(manager.effective_theme, 'dark')
            self.assertIn(DARK.background, APP.styleSheet())
            self.assertNotIn(LIGHT.background, APP.styleSheet())
            manager.set_preference('system')
            self.assertIn(manager.effective_theme, ('light', 'dark'))
        finally:
            APP.setStyleSheet(previous)

    def test_components_expose_roles_and_accessible_controls(self):
        button = AppButton('Create Skill', variant='primary')
        card = Card()
        badge = Badge('Connected', status='success')
        header = PageHeader('Visual Skills', 'Teach the app what to recognize.', button)
        try:
            self.assertEqual(button.property('variant'), 'primary')
            self.assertEqual(button.accessibleName(), 'Create Skill')
            self.assertEqual(card.property('role'), 'card')
            self.assertEqual(badge.status, 'success')
            self.assertEqual(header.title_label.text(), 'Visual Skills')
            self.assertIs(button.parent(), header)
        finally:
            header.close()
            card.close()

    def test_toast_disappears_after_timeout(self):
        parent = QWidget()
        toast = ToastManager(parent)
        try:
            toast.show_message('Workflow saved', duration_ms=20)
            self.assertEqual(toast.label.text(), 'Workflow saved')
            self.assertFalse(toast.label.isHidden())
            QTest.qWait(60)
            self.assertTrue(toast.label.isHidden())
        finally:
            parent.close()

    def test_settings_exposes_and_submits_theme(self):
        window = SettingsWindow(Config(theme='dark'))
        submitted = []
        window.submitted.connect(lambda config, key: submitted.append(config))
        try:
            self.assertEqual(window.theme.currentData(), 'dark')
            window.theme.setCurrentIndex(window.theme.findData('light'))
            window.submit()
            self.assertEqual(submitted[0].theme, 'light')
        finally:
            window.close()
