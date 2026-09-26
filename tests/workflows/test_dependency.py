import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox
from src.skills.custom.models import CustomFieldDefinition, CustomSkillDefinition
from src.skills.custom.storage import CustomSkillStorage
from src.ui.skills.skill_manager import SkillManager
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.storage import WorkflowStorage

APP = QApplication.instance() or QApplication([])


class DependencyTests(unittest.TestCase):
    def test_deleting_custom_skill_disables_dependent_workflows(self):
        with tempfile.TemporaryDirectory() as folder:
            skills = CustomSkillStorage(Path(folder) / 'skills.json')
            workflows = WorkflowStorage(Path(folder) / 'workflows.json')
            skills.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'Receipt',
                (CustomFieldDefinition('merchant', 'Merchant', 'string'),), ('copy_json',)))
            workflows.create_workflow(WorkflowDefinition('save', 'Save Receipt',
                WorkflowTrigger('skill_match', 'receipt'), (WorkflowStep('s', 'copy_json'),)))
            manager = SkillManager(skills, None, workflows=workflows)
            self.addCleanup(manager.close)
            manager.custom.setCurrentRow(0)
            with patch('src.ui.skills.skill_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as asked:
                manager.delete_selected()
            self.assertIn('1 Workflow', asked.call_args.args[2])
            self.assertIsNotNone(skills.get_skill('receipt'))
            with patch('src.ui.skills.skill_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
                manager.delete_selected()
            self.assertIsNone(skills.get_skill('receipt'))
            self.assertFalse(workflows.get_workflow('save').enabled)
