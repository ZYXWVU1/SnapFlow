import tempfile
from pathlib import Path
import unittest
from PySide6.QtWidgets import QApplication, QLabel
from custom_skills.helpers import sample
from src.skills.custom.storage import CustomSkillStorage
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.ui.skills.skill_editor import SkillEditor
from src.ui.skills.skill_manager import SkillManager
from src.ui.result_window import ResultWindow

APP = QApplication.instance() or QApplication([])


class CustomUITests(unittest.TestCase):
    def test_editor_populates_reorders_and_preserves_ids(self):
        editor = SkillEditor(sample())
        self.addCleanup(editor.close)
        editor.fields.table.item(0, 0).setText('Shop')
        editor.fields.table.selectRow(0)
        editor.fields.move_field(1)
        result = editor.definition()
        self.assertEqual([f.id for f in result.fields], ['total', 'merchant'])
        self.assertEqual(result.fields[1].label, 'Shop')
        self.assertEqual(result.id, 'receipt_tracker')
        self.assertIn('Shop', editor.preview.toPlainText())

    def test_editor_validation_and_new_ids(self):
        editor = SkillEditor()
        self.addCleanup(editor.close)
        editor.submit()
        self.assertTrue(editor.error.text())
        editor.name.setText('Package Tracker')
        editor.detection.setPlainText('A package tracking page')
        editor.fields.add_field()
        editor.fields.table.item(0, 0).setText('Order ID')
        definition = editor.definition()
        self.assertEqual(definition.fields[0].id, 'order_id')

    def test_result_snapshot_renders_missing_required_and_actions(self):
        window = ResultWindow('smart', False)
        self.addCleanup(window.close)
        window.set_skill_result(RuntimeCustomSkill(sample()).parse('{"total":4}'))
        self.assertIn('Merchant', window.structured.section_titles)
        self.assertIn('Not detected', [w.text() for w in window.structured.findChildren(QLabel)])
        self.assertIn('Save CSV', window.action_buttons)
        window.action_buttons['Copy Plain Text'].click()
        self.assertIn('Total paid: 4', APP.clipboard().text())

    def test_manager_lists_builtins_separately_and_refreshes(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = CustomSkillStorage(Path(folder) / 'skills.json')
            storage.create_skill(sample())
            manager = SkillManager(storage, None)
            self.addCleanup(manager.close)
            self.assertEqual(manager.builtins.count(), 4)
            self.assertEqual(manager.custom.count(), 1)
            manager.custom.setCurrentRow(0)
            manager.toggle_selected()
            self.assertFalse(storage.get_skill('receipt_tracker').enabled)

    def test_stale_editor_cannot_restore_deleted_or_overwrite_changed_skill(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = CustomSkillStorage(Path(folder) / 'skills.json')
            storage.create_skill(sample())
            manager = SkillManager(storage, None)
            self.addCleanup(manager.close)
            editor = manager.show_editor(sample())
            storage.delete_skill(sample().id)
            editor.submit()
            self.assertIsNone(storage.get_skill(sample().id))
            self.assertIn('changed or deleted', editor.error.text())
            storage.create_skill(sample())
            storage.set_enabled(sample().id, False)
            editor.submit()
            self.assertFalse(storage.get_skill(sample().id).enabled)
