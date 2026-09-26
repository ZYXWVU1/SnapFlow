import unittest

from PySide6.QtWidgets import QApplication, QScrollArea

from src.ui.skills.skill_editor import SkillEditor
from src.ui.workflows.workflow_editor import WorkflowEditor
from src.ui.settings_window import SettingsWindow
from src.config import Config
from src.skills.custom.storage import CustomSkillStorage
from src.skills.registry import SkillRegistry
import tempfile
from pathlib import Path


APP = QApplication.instance() or QApplication([])


class ModernEditorTests(unittest.TestCase):
    def test_skill_editor_scrolls_and_uses_shared_section_cards(self):
        editor = SkillEditor()
        try:
            self.assertIsInstance(editor.content_scroll, QScrollArea)
            self.assertEqual(editor.identity_card.property('role'), 'card')
            self.assertEqual(editor.fields_card.property('role'), 'card')
        finally:
            editor.close()

    def test_workflow_editor_uses_shared_header(self):
        with tempfile.TemporaryDirectory() as folder:
            skills = SkillRegistry(CustomSkillStorage(Path(folder) / 'skills.json'))
            editor = WorkflowEditor(skills)
            try:
                self.assertEqual(editor.page_header.title_label.text(), 'Create Workflow')
            finally:
                editor.close()

    def test_settings_editor_groups_capture_ai_and_appearance(self):
        editor = SettingsWindow(Config())
        try:
            self.assertIsInstance(editor.content_scroll, QScrollArea)
            self.assertEqual(editor.capture_card.property('role'), 'card')
            self.assertEqual(editor.ai_card.property('role'), 'card')
            self.assertEqual(editor.appearance_card.property('role'), 'card')
        finally:
            editor.close()
