import tempfile
import unittest
from pathlib import Path
from src.skill_actions import ActionResult
from src.skills.custom.models import CustomFieldDefinition, CustomSkillDefinition
from src.skills.custom.storage import CustomSkillStorage
from src.skills.registry import SKILLS, SkillRegistry
from src.workflows.context import WorkflowContext
from src.workflows.effects import WorkflowEffects
from src.workflows.executor import WorkflowExecutor
from src.workflows.models import WorkflowCondition, WorkflowDefinition, WorkflowStep, WorkflowTrigger


class WorkflowScenarios(unittest.TestCase):
    def test_custom_internship_sheet_mapping_dry_run_and_suggest(self):
        with tempfile.TemporaryDirectory() as folder:
            skills = CustomSkillStorage(Path(folder) / 'skills.json')
            skills.create_skill(CustomSkillDefinition('internship', 'Internship Tracker', '', 'Job listing',
                (CustomFieldDefinition('company', 'Company', 'string'),
                 CustomFieldDefinition('position', 'Position', 'string')), ('copy_markdown',)))
            registry = SkillRegistry(skills)
            result = registry.get('internship').parse('{"company":"Microsoft","position":"SWE Intern"}')
            workflow = WorkflowDefinition('sheet_job', 'Track Internship',
                WorkflowTrigger('skill_match', 'internship'),
                (WorkflowStep('sheet', 'google_sheets_append_row', {
                    'spreadsheet_id': 'spreadsheet123456', 'tab': 'Applications',
                    'columns': [{'column': 'Company', 'field': 'company'},
                                {'column': 'Position', 'field': 'position'}]}),))
            calls = []
            class Service:
                def execute_action(self, action_id, source, config):
                    calls.append((action_id, source.data, config))
                    return ActionResult(True, 'Row added to Google Sheets.')
            executor = WorkflowExecutor(registry, WorkflowEffects(integration_service=Service()).execute)
            preview = executor.execute(workflow, WorkflowContext.from_result(result), preview=True)
            self.assertEqual(preview.status, 'success')
            self.assertIn('Company: Microsoft', preview.steps[0].preview)
            self.assertEqual(calls, [])
            actual = executor.execute(workflow, WorkflowContext.from_result(result))
            self.assertEqual(actual.status, 'success')
            self.assertEqual(len(calls), 1)

    def test_custom_internship_suggest_chain_and_dry_run(self):
        with tempfile.TemporaryDirectory() as folder:
            skills = CustomSkillStorage(Path(folder) / 'skills.json')
            skills.create_skill(CustomSkillDefinition('internship', 'Internship Tracker', '', 'Job listing',
                (CustomFieldDefinition('company', 'Company', 'string'),
                 CustomFieldDefinition('position', 'Position', 'string')),
                ('copy_markdown',)))
            registry = SkillRegistry(skills)
            result = registry.get('internship').parse('{"company":"Microsoft","position":"SWE Intern"}')
            path = Path(folder) / 'jobs.csv'
            workflow = WorkflowDefinition('save_job', 'Save Internship', WorkflowTrigger('skill_match', 'internship'),
                (WorkflowStep('csv', 'append_csv', {'file_path': str(path)}),
                 WorkflowStep('markdown', 'copy_markdown'),
                 WorkflowStep('notify', 'show_notification', {'title': 'Saved', 'message': '{company} — {position}'})))
            effects_seen = []
            def ui(kind, first, second):
                effects_seen.append((kind, first, second))
                return ActionResult(True)
            executor = WorkflowExecutor(registry, WorkflowEffects(ui).execute)
            context = WorkflowContext.from_result(result)
            preview = executor.execute(workflow, context, preview=True)
            self.assertEqual(preview.status, 'success')
            self.assertFalse(path.exists())
            self.assertEqual(effects_seen, [])
            actual = executor.execute(workflow, context)
            self.assertEqual(actual.status, 'success')
            self.assertIn('Microsoft', path.read_text(encoding='utf-8'))
            self.assertEqual([kind for kind, _, _ in effects_seen], ['copy', 'notification'])
            self.assertEqual(effects_seen[-1][2], 'Microsoft — SWE Intern')

    def test_assignment_auto_conditions_and_ics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'homework.ics'
            workflow = WorkflowDefinition('calendar', 'Assignment Calendar', WorkflowTrigger('skill_match', 'assignment'),
                (WorkflowStep('ics', 'create_ics', {'file_path': str(path)}),),
                conditions=(WorkflowCondition('due_date', 'exists'),), auto_run=True)
            executor = WorkflowExecutor(SKILLS, WorkflowEffects().execute)
            missing = SKILLS['assignment'].parse('{"title":"HW"}')
            self.assertEqual(executor.execute(workflow, WorkflowContext.from_result(missing)).status, 'skipped')
            self.assertFalse(path.exists())
            dated = SKILLS['assignment'].parse('{"title":"HW","due_date":"2026-09-28"}')
            self.assertEqual(executor.execute(workflow, WorkflowContext.from_result(dated)).status, 'success')
            self.assertIn(b'BEGIN:VCALENDAR', path.read_bytes())

    def test_continue_after_bad_file_destination(self):
        result = SKILLS['assignment'].parse('{"title":"HW"}')
        effects_seen = []
        def ui(kind, first, second):
            effects_seen.append(kind)
            return ActionResult(True)
        workflow = WorkflowDefinition('partial', 'Partial', WorkflowTrigger('skill_match', 'assignment'),
            (WorkflowStep('bad', 'append_csv', {'file_path': 'Z:/nonexistent/jobs.csv'}),
             WorkflowStep('copy', 'copy_markdown')), failure_policy='continue')
        execution = WorkflowExecutor(SKILLS, WorkflowEffects(ui).execute).execute(workflow, WorkflowContext.from_result(result))
        self.assertEqual(execution.status, 'partial_success')
        self.assertEqual([step.status for step in execution.steps], ['failed', 'success'])
        self.assertEqual(effects_seen, ['copy'])

    def test_table_workflow_saves_csv_without_formula_cells(self):
        result = SKILLS['table'].parse('{"headers":["Value"],"rows":[["=1+1"]]}')
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'table.csv'
            workflow = WorkflowDefinition('table_export', 'Table Export',
                WorkflowTrigger('skill_match', 'table'),
                (WorkflowStep('save', 'save_csv', {'file_path': str(destination)}),))
            execution = WorkflowExecutor(SKILLS, WorkflowEffects().execute).execute(
                workflow, WorkflowContext.from_result(result))
            self.assertEqual(execution.status, 'success')
            self.assertIn("'=1+1", destination.read_text(encoding='utf-8'))
