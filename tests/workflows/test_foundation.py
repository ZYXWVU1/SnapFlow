import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from src.skills.registry import SKILLS
from src.skill_actions import ActionResult
from src.workflows.models import WorkflowDefinition, WorkflowTrigger, WorkflowStep, WorkflowCondition
from src.workflows.context import WorkflowContext
from src.workflows.conditions import evaluate_condition
from src.workflows.validator import validate_workflow
from src.workflows.storage import WorkflowStorage
from src.workflows.executor import WorkflowExecutor
from src.workflows.engine import WorkflowEngine


def workflow(**changes):
    values = dict(id='homework', name='Homework', trigger=WorkflowTrigger('skill_match', 'assignment'),
                  steps=(WorkflowStep('one', 'copy_markdown'), WorkflowStep('two', 'copy_details')))
    return WorkflowDefinition(**(values | changes))


def context():
    return WorkflowContext.from_result(SKILLS['assignment'].parse('{"title":"HW","points":10}'), request_id='r1')


class FoundationTests(unittest.TestCase):
    def test_roundtrip_and_strict_structure(self):
        w = workflow()
        self.assertEqual(WorkflowDefinition.from_dict(w.to_dict()), w)
        for change in ({'enabled': 1}, {'version': True}, {'failure_policy': 'retry'}, {'name': ''},
                       {'steps': []}, {'steps': [w.steps[0].to_dict()] * 2}, {'auto_run': 'yes'},
                       {'conditions': {}}, {'conditions': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                WorkflowDefinition.from_dict(w.to_dict() | change)

    def test_conditions(self):
        cases = [('exists', None, None, False), ('not_exists', None, None, True),
                 ('is_empty', [], None, True), ('is_not_empty', 0, None, True),
                 ('equals', 'REMOTE', 'remote', True), ('not_equals', 'A', 'B', True),
                 ('contains', 'Remote job', 'REMOTE', True), ('not_contains', 'Boston', 'Remote', True),
                 ('greater_than', 10, 5, True), ('less_than', 2, 5, True),
                 ('greater_than', True, 0, False), ('less_than', '2', 5, False),
                 ('not_contains', None, 'x', False), ('equals', True, 1, False)]
        for op, actual, expected, answer in cases:
            with self.subTest(op=op, actual=actual):
                self.assertEqual(evaluate_condition(WorkflowCondition('x', op, expected), {'x': actual}), answer)
        self.assertTrue(evaluate_condition(WorkflowCondition('x', 'not_exists'), {}))

    def test_validation_uses_skill_schema_and_actions(self):
        self.assertEqual(validate_workflow(workflow(), SKILLS), [])
        for w in [workflow(trigger=WorkflowTrigger('skill_match', 'missing')),
                  workflow(conditions=(WorkflowCondition('missing', 'exists'),)),
                  workflow(conditions=(WorkflowCondition('title', 'greater_than', 5),)),
                  workflow(steps=(WorkflowStep('a', 'missing'),)),
                  workflow(steps=(WorkflowStep('a', 'copy_csv'),)),
                  workflow(steps=(WorkflowStep('a', 'copy_details', {'shell': 'bad'}),)),
                  workflow(steps=(WorkflowStep('a', 'create_ics'),)),
                  workflow(auto_run=True, steps=(WorkflowStep('a', 'ask_ai'),))]:
            with self.subTest(w=w):
                self.assertTrue(validate_workflow(w, SKILLS))
        unknown_template = workflow(steps=(WorkflowStep('a', 'show_notification',
            {'title': '{typo}', 'message': 'Done'}),))
        self.assertTrue(validate_workflow(unknown_template, SKILLS))

    def test_snapshot_and_order(self):
        ctx = context()
        calls = []
        def execute(action, result, config):
            calls.append((action.id, result.data['title']))
            result.data['title'] = 'mutated'
            return ActionResult(True)
        result = WorkflowExecutor(SKILLS, execute).execute(workflow(), ctx)
        self.assertEqual(result.status, 'success')
        self.assertEqual(calls, [('copy_markdown', 'HW'), ('copy_details', 'HW')])
        self.assertEqual(ctx.result.data['title'], 'HW')
        self.assertEqual([s.step_id for s in result.steps], ['one', 'two'])
        self.assertIsNotNone(result.finished_at)

    def test_failure_policies_and_cancellation(self):
        for policy, expected, statuses in [('stop', 'failed', ['failed', 'skipped']),
                                           ('continue', 'partial_success', ['failed', 'success'])]:
            def execute(action, result, config):
                if action.id == 'copy_markdown':
                    raise OSError('private payload')
                return ActionResult(True)
            r = WorkflowExecutor(SKILLS, execute).execute(workflow(failure_policy=policy), context())
            self.assertEqual(r.status, expected)
            self.assertEqual([s.status for s in r.steps], statuses)
            self.assertNotIn('private payload', r.steps[0].error)
        cancel = Event()
        def execute(action, result, config):
            cancel.set()
            return ActionResult(True)
        executor = WorkflowExecutor(SKILLS, execute)
        r = executor.execute(workflow(), context(), cancel=cancel)
        self.assertEqual(r.status, 'cancelled')
        self.assertEqual([s.status for s in r.steps], ['success', 'skipped'])
        self.assertEqual(executor.execute(workflow(), context(), cancel=cancel).status, 'cancelled')

    def test_skipping_preflight_and_preview_have_no_effects(self):
        def forbidden(*args):
            self.fail('unexpected side effect')
        executor = WorkflowExecutor(SKILLS, forbidden)
        self.assertEqual(executor.execute(workflow(enabled=False), context()).status, 'skipped')
        self.assertEqual(executor.execute(workflow(conditions=(WorkflowCondition('points', 'greater_than', 20),)), context()).status, 'skipped')
        self.assertEqual(executor.execute(workflow(steps=(WorkflowStep('a', 'unknown'),)), context()).status, 'failed')
        r = executor.execute(workflow(), context(), preview=True)
        self.assertEqual(r.status, 'success')
        self.assertTrue(r.preview)
        self.assertIn('HW', r.steps[0].preview)
        disabled_preview = executor.execute(workflow(enabled=False), context(), preview=True)
        self.assertEqual(disabled_preview.status, 'success')
        self.assertTrue(disabled_preview.steps[0].preview)

    def test_storage_atomic_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workflows.json'
            storage = WorkflowStorage(path)
            storage.create_workflow(workflow())
            self.assertEqual(WorkflowStorage(path).get_workflow('homework'), workflow())
            storage.set_enabled('homework', False)
            self.assertFalse(storage.get_workflow('homework').enabled)
            storage.delete_workflow('homework')
            self.assertEqual(storage.list_workflows(), [])
            path.write_text('broken', encoding='utf-8')
            storage = WorkflowStorage(path)
            self.assertTrue(storage.warning)
            storage.create_workflow(workflow())
            self.assertEqual(next(Path(folder).glob('*.bak')).read_text(), 'broken')
            original = '{"version":2,"workflows":[]}'
            path.write_text(original)
            with self.assertRaises(ValueError):
                WorkflowStorage(path).create_workflow(workflow())
            self.assertEqual(path.read_text(), original)

    def test_engine_matches_locally_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = WorkflowStorage(Path(folder) / 'workflows.json')
            for w in (workflow(), workflow(id='second'), workflow(id='off', enabled=False)):
                storage.create_workflow(w)
            engine = WorkflowEngine(storage, WorkflowExecutor(SKILLS, lambda *a: ActionResult(True)))
            self.assertEqual([w.id for w in engine.matching('assignment')], ['homework', 'second'])
            self.assertEqual([r.workflow_id for r in engine.execute_matching(context())], ['homework', 'second'])

    def test_builtin_schema_types_are_validated(self):
        self.assertEqual(validate_workflow(workflow(trigger=WorkflowTrigger('skill_match', 'code_error'),
            steps=(WorkflowStep('one', 'copy_error'),),
            conditions=(WorkflowCondition('line', 'greater_than', 2),)), SKILLS), [])
        for field, op, value in [('headers', 'contains', 'name'), ('title', 'equals', 12)]:
            w = workflow(trigger=WorkflowTrigger('skill_match', 'table'),
                steps=(WorkflowStep('one', 'copy_csv'),), conditions=(WorkflowCondition(field, op, value),))
            self.assertTrue(validate_workflow(w, SKILLS))

    def test_large_integers_do_not_crash_conditions(self):
        self.assertTrue(evaluate_condition(WorkflowCondition('x', 'greater_than', 2), {'x': 10 ** 400}))
        self.assertEqual(validate_workflow(workflow(conditions=(WorkflowCondition('points', 'less_than', 10 ** 400),)), SKILLS), [])

    def test_preview_describes_file_destination(self):
        result = SKILLS['assignment'].parse('{"title":"HW","due_date":"2026-09-28"}')
        w = workflow(steps=(WorkflowStep('a', 'create_ics', {'file_path': 'C:/exports/homework.ics'}),))
        r = WorkflowExecutor(SKILLS).execute(w, WorkflowContext.from_result(result), preview=True)
        self.assertEqual(r.status, 'success')
        self.assertIn('C:/exports/homework.ics', r.steps[0].preview)

    def test_failed_storage_replace_keeps_disk_and_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workflows.json'
            storage = WorkflowStorage(path)
            storage.create_workflow(workflow())
            original = path.read_bytes()
            with patch('src.workflows.storage.os.replace', side_effect=OSError('denied')):
                with self.assertRaises(OSError):
                    storage.set_enabled('homework', False)
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(storage.get_workflow('homework').enabled)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_custom_skill_and_dependency_changes(self):
        from src.skills.custom.models import CustomFieldDefinition, CustomSkillDefinition
        from src.skills.custom.storage import CustomSkillStorage
        from src.skills.registry import SkillRegistry
        with tempfile.TemporaryDirectory() as folder:
            storage = CustomSkillStorage(Path(folder) / 'skills.json')
            storage.create_skill(CustomSkillDefinition('receipt', 'Receipt', '', 'A receipt',
                (CustomFieldDefinition('total', 'Total', 'number'),), ('copy_json',)))
            registry = SkillRegistry(storage)
            w = workflow(trigger=WorkflowTrigger('skill_match', 'receipt'),
                steps=(WorkflowStep('one', 'copy_json'),), conditions=(WorkflowCondition('total', 'greater_than', 5),))
            result = registry.get('receipt').parse('{"total":10}')
            executor = WorkflowExecutor(registry, lambda *args: ActionResult(True))
            self.assertEqual(executor.execute(w, WorkflowContext.from_result(result)).status, 'success')
            storage.set_enabled('receipt', False)
            self.assertEqual(executor.execute(w, WorkflowContext.from_result(result)).status, 'failed')

    def test_disabled_step_and_all_failures(self):
        calls = []
        executor = WorkflowExecutor(SKILLS, lambda action, *args: (calls.append(action.id) or ActionResult(False)))
        r = executor.execute(workflow(failure_policy='continue'), context())
        self.assertEqual(r.status, 'failed')
        self.assertEqual(len(calls), 2)
        calls.clear()
        r = executor.execute(workflow(steps=(WorkflowStep('one', 'copy_markdown', enabled=False),
                                            WorkflowStep('two', 'copy_details'))), context())
        self.assertEqual(calls, ['copy_details'])
        self.assertEqual([s.status for s in r.steps], ['skipped', 'failed'])

    def test_malformed_stored_conditions_are_not_unconditional(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workflows.json'
            raw = workflow().to_dict() | {'conditions': {}}
            path.write_text(json.dumps({'version': 1, 'workflows': [raw]}), encoding='utf-8')
            storage = WorkflowStorage(path)
            self.assertEqual(storage.list_workflows(), [])
            self.assertTrue(storage.warning)
            storage.create_workflow(workflow())
            self.assertEqual(len(list(Path(folder).glob('*.bak'))), 1)

    def test_middle_failure_and_registry_refresh(self):
        calls = []
        def execute(action, *args):
            calls.append(action.id)
            return ActionResult(action.id != 'copy_details')
        w = workflow(steps=(WorkflowStep('one', 'copy_markdown'), WorkflowStep('two', 'copy_details'),
                            WorkflowStep('three', 'copy_markdown')))
        r = WorkflowExecutor(SKILLS, execute).execute(w, context())
        self.assertEqual([s.status for s in r.steps], ['success', 'failed', 'skipped'])
        self.assertEqual(calls, ['copy_markdown', 'copy_details'])
        with tempfile.TemporaryDirectory() as folder:
            storage = WorkflowStorage(Path(folder) / 'workflows.json')
            engine = WorkflowEngine(storage, WorkflowExecutor(SKILLS))
            self.assertEqual(engine.matching('assignment'), [])
            storage.create_workflow(w)
            self.assertEqual(len(engine.matching('assignment')), 1)
            storage.set_enabled(w.id, False)
            self.assertEqual(engine.matching('assignment'), [])
