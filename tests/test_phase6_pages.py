import unittest
from types import SimpleNamespace
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.config import Config
from src.ui.design.theme import ThemeManager
from src.ui.main_window import MainWindow
from src.ui.pages.common import NamedCard
from src.app import ApplicationController
from src.skills.custom.storage import CustomSkillStorage
from src.skills.custom.models import CustomSkillDefinition, CustomFieldDefinition


APP = QApplication.instance() or QApplication([])


class MemorySkills:
    def __init__(self, names=()):
        self.items = [SimpleNamespace(id=name.lower().replace(' ', '_'), name=name,
                                      description='Recognize important screenshots',
                                      enabled=True, fields=[1, 2], actions=['copy_text'])
                      for name in names]

    def list_skills(self):
        return self.items


class MemoryWorkflows:
    def __init__(self, names=()):
        self.items = [SimpleNamespace(id=name.lower().replace(' ', '_'), name=name,
                                      trigger=SimpleNamespace(skill_id='assignment'),
                                      steps=[1, 2], auto_run=False, enabled=True)
                      for name in names]

    def list_workflows(self):
        return self.items


class MemoryHistory:
    def __init__(self, entries=()):
        self.entries = list(entries)

    def list_entries(self):
        return self.entries


class Phase6PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = ThemeManager(APP, 'light')

    def test_skill_and_workflow_search_and_empty_states(self):
        skills = MemorySkills(('Internship Tracker', 'Receipt Tracker'))
        workflows = MemoryWorkflows(('Assignment Calendar', 'Save Internship'))
        window = MainWindow('ctrl+shift+s', skills_storage=skills, workflows_storage=workflows)
        try:
            window.show()
            window.open_page('skills')
            page = window.pages['skills']
            self.assertEqual(len(page.visible_custom_cards()), 2)
            self.assertEqual(len([card for card in page.findChildren(NamedCard)
                                  if card.name == 'Internship Tracker' and not card.isHidden()]), 1)
            page.search.setText('receipt')
            self.assertEqual([card.name for card in page.visible_custom_cards()], ['Receipt Tracker'])
            window.open_page('workflows')
            page = window.pages['workflows']
            self.assertEqual(len([card for card in page.findChildren(NamedCard)
                                  if card.name == 'Save Internship' and not card.isHidden()]), 1)
            page.search.setText('calendar')
            self.assertEqual([card.name for card in page.visible_cards()], ['Assignment Calendar'])
        finally:
            window.close()
        empty = MainWindow('ctrl+shift+s', skills_storage=MemorySkills(), workflows_storage=MemoryWorkflows())
        try:
            empty.show()
            empty.open_page('skills')
            self.assertFalse(empty.pages['skills'].empty_state.isHidden())
            empty.open_page('workflows')
            self.assertFalse(empty.pages['workflows'].empty_state.isHidden())
        finally:
            empty.close()

    def test_history_and_settings_reflect_current_data(self):
        history = MemoryHistory((
            {'workflow_name': 'Earlier', 'status': 'success', 'started_at': '2026-09-20', 'steps': []},
            {'workflow_name': 'Latest', 'status': 'failed', 'started_at': '2026-09-25', 'steps': []},
        ))
        window = MainWindow('ctrl+shift+s', history=history, config=Config(theme='dark'))
        try:
            window.show()
            window.open_page('history')
            self.assertEqual(window.pages['history'].entries[0].name, 'Latest')
            window.open_page('settings')
            self.assertIn('Ctrl + Shift + S', window.pages['settings'].hotkey_label.text())
            self.assertEqual(window.pages['settings'].theme_label.text(), 'Dark')
        finally:
            window.close()

    def test_overview_cards_offer_direct_edit_and_test(self):
        window = MainWindow('ctrl+shift+s', skills_storage=MemorySkills(('Internship Tracker',)),
                            workflows_storage=MemoryWorkflows(('Save Internship',)))
        events = []
        window.skill_edit_requested.connect(lambda value: events.append(('skill_edit', value)))
        window.workflow_test_requested.connect(lambda value: events.append(('workflow_test', value)))
        try:
            window.open_page('skills')
            self.assertEqual(len(window.pages['skills'].builtin_cards), 4)
            window.pages['skills'].cards[0].edit_button.click()
            window.open_page('workflows')
            window.pages['workflows'].cards[0].test_button.click()
            self.assertEqual(events, [('skill_edit', 'internship_tracker'),
                                      ('workflow_test', 'save_internship')])
        finally:
            window.close()

    def test_controller_opens_selected_skill_editor_from_page(self):
        with tempfile.TemporaryDirectory() as folder, patch('src.app.load_config', return_value=Config()):
            storage = CustomSkillStorage(Path(folder) / 'skills.json')
            storage.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'Receipt screenshot',
                (CustomFieldDefinition('total', 'Total', 'number'),), ('copy_json',)))
            with patch('src.app.CustomSkillStorage', return_value=storage):
                controller = ApplicationController(APP, preview=True)
            try:
                controller.main_window.pages['skills'].refresh()
                controller.main_window.pages['skills'].cards[0].edit_button.click()
                self.assertEqual(controller.skill_manager.dialogs[-1].original.id, 'receipt')
            finally:
                controller.hotkeys.close()
                controller.tray.hide()
                controller.main_window.close()
                controller.result.close()
                if controller.skill_manager:
                    controller.skill_manager.close()
