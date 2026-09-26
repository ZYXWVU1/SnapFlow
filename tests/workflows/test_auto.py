import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from src.app import ApplicationController
from src.config import Config
from src.skills.registry import SKILLS
from src.ui.workflows.workflow_manager import WorkflowManager
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.storage import WorkflowStorage
from src.workflows.registry import WorkflowRegistry
from src.workflows.history import WorkflowHistory

APP = QApplication.instance() or QApplication([])


class AutoTests(unittest.TestCase):
    def test_first_auto_save_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            from src.skills.custom.storage import CustomSkillStorage
            from src.skills.registry import SkillRegistry
            manager = WorkflowManager(WorkflowStorage(Path(folder) / 'workflows.json'),
                SkillRegistry(CustomSkillStorage(Path(folder) / 'skills.json')), allow_auto=True)
            self.addCleanup(manager.close)
            editor = manager.create_workflow()
            editor.name.setText('Auto copy')
            editor.trigger.setCurrentIndex(editor.trigger.findData('assignment'))
            editor.add_step('copy_markdown')
            editor.mode.setCurrentIndex(editor.mode.findData(True))
            with patch('src.ui.workflows.workflow_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
                editor.submit()
            self.assertEqual(manager.storage.list_workflows(), [])
            with patch('src.ui.workflows.workflow_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
                editor.submit()
            self.assertTrue(manager.storage.list_workflows()[0].auto_run)

    def test_external_auto_confirmation_names_cloud_action(self):
        with tempfile.TemporaryDirectory() as folder:
            from src.skills.custom.storage import CustomSkillStorage
            from src.skills.registry import SkillRegistry
            manager = WorkflowManager(WorkflowStorage(Path(folder) / 'workflows.json'),
                SkillRegistry(CustomSkillStorage(Path(folder) / 'skills.json')), allow_auto=True)
            self.addCleanup(manager.close)
            editor = manager.create_workflow()
            editor.name.setText('Auto task')
            editor.add_step('todoist_create_task')
            editor.config_inputs['title'].setText('Do {title}')
            editor.mode.setCurrentIndex(editor.mode.findData(True))
            with patch('src.ui.workflows.workflow_manager.QMessageBox.question',
                       return_value=QMessageBox.StandardButton.Yes) as question:
                editor.submit()
            self.assertEqual(len(manager.storage.list_workflows()), 1)
            self.assertIn('Create Todoist Task', question.call_args.args[2])

    def test_skill_result_auto_dispatch_once_per_request(self):
        APP.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as folder, patch('src.app.load_config', return_value=Config(hotkey='ctrl+alt+shift+7')):
            controller = ApplicationController(APP, preview=True)
            try:
                controller.workflow_storage = WorkflowStorage(Path(folder) / 'workflows.json')
                controller.workflow_history = WorkflowHistory(Path(folder) / 'history.json')
                controller.workflow_registry = WorkflowRegistry(controller.workflow_storage)
                controller.result.set_workflow_registry(controller.workflow_registry)
                controller.workflow_storage.create_workflow(WorkflowDefinition('w', 'Auto copy',
                    WorkflowTrigger('skill_match', 'assignment'), (WorkflowStep('s', 'copy_markdown'),), auto_run=True))
                result = SKILLS['assignment'].parse('{"title":"HW"}')
                controller.request_id = 91
                controller.workers[91] = SimpleNamespace(question='')
                controller.analysis_finished(91, result, False)
                controller.dispatch_auto_workflows(result)
                self.assertEqual(len(controller.workflow_jobs), 1)
                for _ in range(100):
                    APP.processEvents()
                    if not controller.workflow_jobs:
                        break
                    QTest.qWait(10)
                self.assertFalse(controller.workflow_jobs)
                self.assertIn('success', controller.result.workflow_status.text())
                self.assertEqual(controller.result.workflow_buttons, [])
                controller.dispatch_auto_workflows(result)
                self.assertFalse(controller.workflow_jobs)
                self.assertEqual(len(controller.workflow_history.list_entries()), 1)
            finally:
                controller.hotkeys.close()
                controller.tray.hide()
                controller.result.close()

    def test_multiple_auto_results_preserve_earlier_failure(self):
        APP.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as folder, patch('src.app.load_config', return_value=Config(hotkey='ctrl+alt+shift+7')):
            controller = ApplicationController(APP, preview=True)
            try:
                controller.workflow_storage = WorkflowStorage(Path(folder) / 'workflows.json')
                controller.workflow_history = WorkflowHistory(Path(folder) / 'history.json')
                controller.workflow_registry = WorkflowRegistry(controller.workflow_storage)
                controller.result.set_workflow_registry(controller.workflow_registry)
                for definition in (
                    WorkflowDefinition('bad', 'Bad destination', WorkflowTrigger('skill_match', 'assignment'),
                        (WorkflowStep('a', 'append_csv', {'file_path': 'Z:/missing/jobs.csv'}),), auto_run=True),
                    WorkflowDefinition('good', 'Copy summary', WorkflowTrigger('skill_match', 'assignment'),
                        (WorkflowStep('b', 'copy_markdown'),), auto_run=True)):
                    controller.workflow_storage.create_workflow(definition)
                result = SKILLS['assignment'].parse('{"title":"HW"}')
                controller.request_id = 92
                controller.workers[92] = SimpleNamespace(question='')
                controller.analysis_finished(92, result, False)
                self.assertEqual(len(controller.workflow_jobs), 2)
                for _ in range(100):
                    APP.processEvents()
                    if not controller.workflow_jobs:
                        break
                    QTest.qWait(10)
                status = controller.result.workflow_status.text()
                self.assertIn('Bad destination: failed', status)
                self.assertIn('Copy summary: success', status)
                self.assertFalse(controller.result.workflow_cancel.isVisible())
                self.assertEqual(len(controller.workflow_history.list_entries()), 2)
            finally:
                controller.hotkeys.close()
                controller.tray.hide()
                controller.result.close()
