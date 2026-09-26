import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import time

from src.integrations.models import IntegrationConnection
from src.integrations.registry import IntegrationRegistry
from src.integrations.storage import ConnectionStorage
from src.ui.design.theme import ThemeManager
from src.ui.integrations.integration_page import IntegrationPage
from src.ui.main_window import MainWindow
from src.app import ApplicationController
from src.config import Config
from src.integrations.service import ConnectionOutcome


APP = QApplication.instance() or QApplication([])


class IntegrationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = ThemeManager(APP, 'light')

    def test_cards_reflect_connection_state_and_emit_connect_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = ConnectionStorage(Path(folder) / 'integrations.json')
            page = IntegrationPage(IntegrationRegistry(), storage)
            requested = []
            page.connection_requested.connect(requested.append)
            try:
                page.show()
                self.assertEqual(set(page.cards), {'google', 'todoist'})
                self.assertEqual(page.cards['google'].status_label.text(), 'Not connected')
                page.cards['todoist'].connect_button.click()
                self.assertEqual(requested, ['todoist'])
                storage.save(IntegrationConnection('google', 'connected', 'user@example.com',
                    granted_capabilities=('google_calendar',)))
                page.refresh()
                self.assertIn('user@example.com', page.cards['google'].account_label.text())
                self.assertEqual(page.cards['google'].status_label.text(), 'Connected')
                self.assertTrue(page.cards['google'].connect_button.isVisibleTo(page))
                self.assertEqual(page.cards['google'].connect_button.text(), 'Manage permissions')
            finally:
                page.close()

    def test_shell_uses_registry_page_and_forwards_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = ConnectionStorage(Path(folder) / 'integrations.json')
            window = MainWindow('ctrl+shift+s', integration_registry=IntegrationRegistry(),
                                connection_storage=storage)
            requested = []
            window.integration_connect_requested.connect(requested.append)
            try:
                window.open_page('integrations')
                page = window.pages['integrations']
                self.assertIsInstance(page, IntegrationPage)
                page.cards['google'].connect_button.click()
                self.assertEqual(requested, ['google'])
            finally:
                window.close()

    def test_controller_todoist_connection_runs_in_worker_and_refreshes_card(self):
        folder = tempfile.TemporaryDirectory()
        storage = ConnectionStorage(Path(folder.name) / 'integrations.json')
        with patch('src.app.load_config', return_value=Config()), \
                patch('src.app.ConnectionStorage', return_value=storage):
            controller = ApplicationController(APP, preview=True)
        class Service:
            def __init__(self, storage):
                self.storage = storage
                self.calls = []
            def connect_todoist(self, token):
                self.calls.append(token)
                self.storage.save(IntegrationConnection('todoist', 'connected', 'Todoist account'))
                return ConnectionOutcome(True, 'connected', 'Todoist connected.')
        service = Service(controller.connection_storage)
        controller.integration_service = service
        try:
            dialog = controller.connect_integration('todoist')
            dialog.token.setText('synthetic-token')
            dialog.connect_button.click()
            deadline = time.monotonic() + 2
            while controller.integration_jobs and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(service.calls, ['synthetic-token'])
            self.assertEqual(controller.main_window.pages['integrations'].cards['todoist'].status_label.text(),
                             'Connected')
        finally:
            controller.hotkeys.close()
            controller.tray.hide()
            controller.main_window.close()
            controller.result.close()
            if hasattr(controller, 'integration_pool'):
                controller.integration_pool.waitForDone()
            folder.cleanup()
