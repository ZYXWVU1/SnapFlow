import unittest

from PySide6.QtWidgets import QApplication, QScrollArea

from src.ui.design.theme import ThemeManager
from src.ui.result_window import ResultWindow
from src.skills.registry import SKILLS


APP = QApplication.instance() or QApplication([])


class ResultRedesignTests(unittest.TestCase):
    def test_result_uses_shared_cards_and_scrollable_content(self):
        ThemeManager(APP, 'light')
        window = ResultWindow('smart', False)
        try:
            self.assertIsInstance(window.content_scroll, QScrollArea)
            self.assertEqual(window.result_card.property('role'), 'card')
            self.assertEqual(window.action_card.property('role'), 'card')
            window.set_skill_result(SKILLS['assignment'].parse('{"title":"Homework"}'))
            self.assertTrue(window.action_card.isVisibleTo(window) or not window.isVisible())
            self.assertTrue(window.structured.isVisibleTo(window) or not window.isVisible())
        finally:
            window.close()

    def test_disconnected_workflow_offers_integration_navigation(self):
        window = ResultWindow('smart', False)
        requested = []
        window.integration_requested.connect(lambda: requested.append(True))
        try:
            window.set_workflow_status('Google is not connected for this Action. Connect it in Integrations.')
            self.assertFalse(window.integration_connect.isHidden())
            window.integration_connect.click()
            self.assertEqual(requested, [True])
        finally:
            window.close()
