import threading
import time
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from src.ui.workflows.runner import WorkflowBridge
from src.skills.registry import SKILLS
from src.workflows.context import WorkflowContext
from src.workflows.executor import WorkflowExecutor
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.storage import WorkflowStorage
from pathlib import Path
import tempfile

APP = QApplication.instance() or QApplication([])


class RunnerTests(unittest.TestCase):
    def test_expired_ui_request_cannot_change_clipboard_later(self):
        bridge = WorkflowBridge(Mock(), Mock(), timeout_seconds=.01)
        APP.clipboard().setText('before')
        results = []
        thread = threading.Thread(target=lambda: results.append(bridge.execute('copy', 'after', '')))
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0].success)
        APP.processEvents()
        self.assertEqual(APP.clipboard().text(), 'before')

    def test_started_ui_effect_returns_its_result_even_after_timeout_threshold(self):
        tray = Mock()
        tray.showMessage.side_effect = lambda *_: time.sleep(.03)
        bridge = WorkflowBridge(tray, Mock(), timeout_seconds=.01)
        results = []
        thread = threading.Thread(target=lambda: results.append(bridge.execute('notification', 'Title', 'Message')))
        thread.start()
        for _ in range(100):
            if thread.is_alive():
                APP.processEvents()
            if results:
                break
            time.sleep(.001)
        thread.join(2)
        self.assertEqual(tray.showMessage.call_count, 1)
        self.assertTrue(results[0].success)

    def test_queued_definition_disabled_before_start_has_no_effect(self):
        from src.ui.workflows.runner import WorkflowWorker
        with tempfile.TemporaryDirectory() as folder:
            storage = WorkflowStorage(Path(folder) / 'workflows.json')
            definition = WorkflowDefinition('w', 'Copy', WorkflowTrigger('skill_match', 'assignment'),
                (WorkflowStep('s', 'copy_markdown'),), auto_run=True)
            storage.create_workflow(definition)
            result = SKILLS['assignment'].parse('{"title":"HW"}')
            bridge = Mock()
            worker = WorkflowWorker(definition, WorkflowContext.from_result(result), WorkflowExecutor(SKILLS), bridge,
                                    storage=storage)
            finished = []
            worker.signals.finished.connect(lambda _worker, execution: finished.append(execution))
            storage.set_enabled('w', False)
            worker.run()
            APP.processEvents()
            self.assertEqual(finished[0].status, 'skipped')
            bridge.execute.assert_not_called()
