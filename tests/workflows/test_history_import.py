import json
import tempfile
import unittest
from pathlib import Path
from src.skills.registry import SKILLS
from src.workflows.context import WorkflowContext
from src.workflows.executor import WorkflowExecutor
from src.workflows.history import WorkflowHistory
from src.workflows.import_export import export_workflow, import_workflow
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger


def sample(**changes):
    return WorkflowDefinition(**(dict(id='w', name='Save', trigger=WorkflowTrigger('skill_match', 'assignment'),
       steps=(WorkflowStep('s', 'copy_markdown'),), auto_run=True) | changes))


class HistoryImportTests(unittest.TestCase):
    def test_history_is_bounded_and_contains_no_extracted_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            history = WorkflowHistory(path, limit=2)
            result = SKILLS['assignment'].parse('{"title":"SECRET HOMEWORK"}')
            from src.skill_actions import ActionResult
            executor = WorkflowExecutor(SKILLS, lambda *args: ActionResult(True))
            preview = executor.execute(sample(), WorkflowContext.from_result(result), preview=True)
            history.record(sample(), preview, result.skill_id)
            self.assertEqual(history.list_entries(), [])
            execution = executor.execute(sample(), WorkflowContext.from_result(result))
            for _ in range(3):
                history.record(sample(), execution, result.skill_id)
            self.assertEqual(len(WorkflowHistory(path, limit=2).list_entries()), 2)
            self.assertNotIn('SECRET HOMEWORK', path.read_text(encoding='utf-8'))
            self.assertNotIn('preview', path.read_text(encoding='utf-8'))

    def test_import_always_disables_auto_and_handles_dependencies(self):
        encoded = export_workflow(sample())
        imported, warnings = import_workflow(encoded, existing={'w'}, skills=SKILLS)
        self.assertFalse(imported.auto_run)
        self.assertFalse(imported.enabled)
        self.assertNotEqual(imported.id, 'w')
        self.assertTrue(warnings)
        missing = sample(trigger=WorkflowTrigger('skill_match', 'missing'))
        imported, warnings = import_workflow(export_workflow(missing), skills=SKILLS)
        self.assertFalse(imported.enabled)
        self.assertIn('missing', ' '.join(warnings).lower())
        for invalid in ['{}', json.dumps({'format':'wrong','version':1,'workflow':sample().to_dict()}),
                        json.dumps({'format':'ai-screenshot-helper-workflow','version':2,'workflow':sample().to_dict()})]:
            with self.assertRaises(ValueError):
                import_workflow(invalid, skills=SKILLS)

    def test_unknown_action_import_stays_disabled_for_repair(self):
        invalid = sample(steps=(WorkflowStep('s', 'future_action'),))
        restored, warnings = import_workflow(export_workflow(invalid), skills=SKILLS)
        self.assertFalse(restored.enabled)
        self.assertTrue(any('action' in warning.lower() for warning in warnings))

    def test_malformed_history_entry_cannot_crash_view_or_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            original = '{"version":1,"entries":[{"bad":1}]}'
            path.write_text(original, encoding='utf-8')
            history = WorkflowHistory(path)
            self.assertTrue(history.warning)
            self.assertEqual(history.list_entries(), [])
            self.assertEqual(path.read_text(encoding='utf-8'), original)
