import unittest
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from src.ui.workflows.resource_picker import ResourcePicker
from src.ui.workflows.workflow_editor import WorkflowEditor
from src.skills.registry import SkillRegistry
from src.skills.custom.storage import CustomSkillStorage


APP = QApplication.instance() or QApplication([])


class ResourcePickerTests(unittest.TestCase):
    def test_picker_displays_name_and_saves_stable_id(self):
        picker = ResourcePicker('cal-123')
        try:
            picker.set_options([('School', 'cal-123'), ('Work', 'cal-456')])
            self.assertEqual(picker.combo.currentText(), 'School')
            self.assertEqual(picker.value(), 'cal-123')
            picker.combo.setCurrentIndex(1)
            self.assertEqual(picker.value(), 'cal-456')
            picker.combo.setEditText('custom-cal')
            self.assertEqual(picker.value(), 'custom-cal')
        finally:
            picker.close()

    def test_editor_loads_project_options_in_background(self):
        class Service:
            def list_resources(self, action_id, config):
                time.sleep(0.1)
                return [('School', 'project-123')]
        with tempfile.TemporaryDirectory() as folder:
            editor = WorkflowEditor(SkillRegistry(CustomSkillStorage(Path(folder) / 'skills.json')),
                                    integration_service=Service())
            try:
                editor.add_step('todoist_create_task')
                picker = editor.config_inputs['project_id']
                picker.load_button.click()
                self.assertFalse(picker.load_button.isEnabled())
                for _ in range(100):
                    APP.processEvents()
                    if picker.combo.findData('project-123') >= 0:
                        break
                    QTest.qWait(10)
                self.assertGreaterEqual(picker.combo.findData('project-123'), 0)
                picker.combo.setCurrentIndex(picker.combo.findData('project-123'))
                self.assertEqual(editor.steps[0].config['project_id'], 'project-123')
            finally:
                editor.close()
