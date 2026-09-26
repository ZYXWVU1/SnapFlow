import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.ui.design.theme import ThemeManager
from src.ui.main_window import MainWindow
from src.app import ApplicationController
from src.config import Config


APP = QApplication.instance() or QApplication([])


class Phase6ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = ThemeManager(APP, 'light')

    def test_navigation_and_capture(self):
        window = MainWindow('ctrl+shift+s')
        captures = []
        window.capture_requested.connect(lambda: captures.append(True))
        try:
            window.show()
            APP.processEvents()
            self.assertEqual(window.current_page, 'home')
            self.assertEqual(window.pages['home'].horizontalScrollBar().maximum(), 0)
            self.assertIn('Ctrl + Shift + S', window.hotkey_label.text())
            for page in ('skills', 'workflows', 'integrations', 'history', 'settings'):
                window.open_page(page)
                self.assertEqual(window.current_page, page)
                self.assertEqual(window.stack.currentWidget(), window.pages[page])
            window.capture_button.click()
            self.assertEqual(captures, [True])
        finally:
            window.close()

    def test_narrow_navigation_keeps_accessible_names(self):
        window = MainWindow('ctrl+shift+s')
        try:
            window.resize(680, 620)
            window.show()
            APP.processEvents()
            self.assertEqual(window.nav_buttons['skills'].text(), '')
            self.assertEqual(window.nav_buttons['skills'].accessibleName(), 'Skills')
            window.resize(1000, 680)
            APP.processEvents()
            self.assertEqual(window.nav_buttons['skills'].text(), 'Skills')
        finally:
            window.close()

    def test_feature_pages_offer_existing_view(self):
        window = MainWindow('ctrl+shift+s')
        requested = []
        window.feature_requested.connect(requested.append)
        try:
            window.open_page('skills')
            window.launch_buttons['skills'].click()
            self.assertEqual(requested, ['skills'])
        finally:
            window.close()

    def test_home_summarizes_recent_workflows_and_integrations(self):
        class History:
            def list_entries(self):
                return [{'workflow_name': 'Assignment calendar', 'status': 'success',
                    'started_at': '2026-09-25', 'steps': []}]
        class Connections:
            def get(self, key):
                from src.integrations.models import IntegrationConnection
                return IntegrationConnection('google', 'connected', 'Google account',
                    granted_capabilities=('google_calendar',)) if key == 'google' else None
        window = MainWindow('ctrl+shift+s', history=History(), connection_storage=Connections())
        try:
            window.open_page('home')
            self.assertIn('Assignment calendar', window.recent_workflows.text())
            self.assertIn('Google Calendar: Connected', window.integration_summary.text())
            self.assertIn('Todoist: Not connected', window.integration_summary.text())
        finally:
            window.close()

    def test_controller_tray_and_shell_open_existing_features(self):
        with patch('src.app.load_config', return_value=Config()):
            controller = ApplicationController(APP, preview=True)
        try:
            self.assertFalse(controller.main_window.isVisible())
            controller.home_action.trigger()
            self.assertTrue(controller.main_window.isVisible())
            with patch.object(controller, 'begin_selection'):
                controller.main_window.capture_button.click()
                self.assertTrue(controller.capturing)
                self.assertFalse(controller.main_window.isVisible())
                controller.cancel_selection()
                self.assertTrue(controller.main_window.isVisible())
            for page, attribute in (('skills', 'skill_manager'),
                                    ('workflows', 'workflow_manager'), ('settings', 'settings')):
                controller.main_window.open_page(page)
                controller.main_window.launch_buttons[page].click()
                self.assertTrue(getattr(controller, attribute).isVisible())
        finally:
            controller.hotkeys.close()
            controller.tray.hide()
            controller.main_window.close()
            controller.result.close()
            for name in ('skill_manager', 'workflow_manager', 'settings'):
                widget = getattr(controller, name)
                if widget is not None:
                    widget.close()
