import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from unittest.mock import patch
from src.app import ApplicationController
from src.config import Config
from src.skills.registry import SKILLS
from src.ui.result_window import ResultWindow
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.storage import WorkflowStorage
from src.workflows.registry import WorkflowRegistry
from src.workflows.history import WorkflowHistory
from src.skill_actions import ActionResult

APP = QApplication.instance() or QApplication([])


class SuggestTests(unittest.TestCase):
    def test_cloud_step_confirmation_appears_in_result(self):
        APP.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as folder, patch('src.app.load_config', return_value=Config(hotkey='ctrl+alt+shift+7')):
            controller = ApplicationController(APP, preview=True)
            try:
                controller.workflow_storage = WorkflowStorage(Path(folder) / 'workflows.json')
                controller.workflow_history = WorkflowHistory(Path(folder) / 'history.json')
                controller.workflow_registry = WorkflowRegistry(controller.workflow_storage)
                controller.result.set_workflow_registry(controller.workflow_registry)
                class Service:
                    def execute_action(self, *args):
                        return ActionResult(True, 'Todoist task created.', kind='cloud')
                controller.integration_service = Service()
                controller.workflow_storage.create_workflow(WorkflowDefinition('w', 'Make task',
                    WorkflowTrigger('skill_match', 'assignment'),
                    (WorkflowStep('s', 'todoist_create_task', {'title': '{title}'}),)))
                controller.last_result = SKILLS['assignment'].parse('{"title":"HW"}')
                controller.request_id = 43
                controller.result.set_skill_result(controller.last_result)
                controller.result.workflow_buttons[0].click()
                for _ in range(100):
                    APP.processEvents()
                    if not controller.workflow_jobs:
                        break
                    QTest.qWait(10)
                self.assertIn('Todoist task created.', controller.result.workflow_status.text())
            finally:
                controller.hotkeys.close()
                controller.tray.hide()
                controller.result.close()

    def test_skill_result_shows_matching_suggest_and_direct_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = WorkflowStorage(Path(folder) / 'workflows.json')
            storage.create_workflow(WorkflowDefinition('w', 'Summarize', WorkflowTrigger('skill_match', 'assignment'),
                (WorkflowStep('s', 'copy_markdown'),)))
            window = ResultWindow('smart', False)
            self.addCleanup(window.close)
            window.set_workflow_registry(WorkflowRegistry(storage))
            window.set_skill_result(SKILLS['assignment'].parse('{"title":"HW"}'))
            self.assertIn('Copy Markdown', window.action_buttons)
            self.assertEqual(len(window.workflow_buttons), 1)
            self.assertEqual(window.workflow_buttons[0].text(), 'Run Summarize')
            seen = []
            window.workflow_requested.connect(lambda workflow_id: seen.append(workflow_id))
            window.workflow_buttons[0].click()
            self.assertEqual(seen, ['w'])

    def test_controller_runs_suggest_and_updates_status(self):
        APP.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as folder, patch('src.app.load_config', return_value=Config(hotkey='ctrl+alt+shift+7')):
            controller = ApplicationController(APP, preview=True)
            try:
                controller.workflow_storage = WorkflowStorage(Path(folder) / 'workflows.json')
                controller.workflow_history = WorkflowHistory(Path(folder) / 'history.json')
                controller.workflow_registry = WorkflowRegistry(controller.workflow_storage)
                controller.result.set_workflow_registry(controller.workflow_registry)
                controller.workflow_storage.create_workflow(WorkflowDefinition('w', 'Summarize',
                    WorkflowTrigger('skill_match', 'assignment'), (WorkflowStep('s', 'copy_markdown'),)))
                controller.last_result = SKILLS['assignment'].parse('{"title":"HW"}')
                controller.request_id = 42
                controller.result.set_skill_result(controller.last_result)
                controller.result.workflow_buttons[0].click()
                for _ in range(100):
                    APP.processEvents()
                    if not controller.workflow_jobs:
                        break
                    QTest.qWait(10)
                self.assertFalse(controller.workflow_jobs)
                self.assertIn('success', controller.result.workflow_status.text())
                self.assertIn('HW', APP.clipboard().text())
            finally:
                controller.hotkeys.close()
                controller.tray.hide()
                controller.result.close()
