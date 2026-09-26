import unittest
from dataclasses import replace

from src.skill_actions import ActionResult
from src.skills.base import SkillResult
from src.skills.registry import SKILLS
from src.workflows.context import WorkflowContext
from src.workflows.executor import WorkflowExecutor
from src.workflows.models import WorkflowDefinition, WorkflowStep, WorkflowTrigger
from src.workflows.effects import WorkflowEffects


class CloudWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = SkillResult('assignment', 'Assignment', .9,
            {'title': 'Homework', 'due_date': '2026-09-30'}, [])
        self.config = {'title': 'Do {title}', 'due_date_field': 'due_date'}
        self.workflow = WorkflowDefinition('cloud1', 'Assignment task', WorkflowTrigger('skill_match', 'assignment'),
            (WorkflowStep('step1', 'todoist_create_task', self.config),))

    def test_dry_run_previews_without_external_write(self):
        class Service:
            def execute_action(self, *args):
                raise AssertionError('Dry Run made an external write')
        effects = WorkflowEffects(integration_service=Service())
        outcome = WorkflowExecutor(SKILLS, effects.execute).execute(self.workflow,
            WorkflowContext.from_result(self.source), preview=True)
        self.assertEqual(outcome.status, 'success')
        self.assertIn('Would create Todoist task', outcome.steps[0].preview)

    def test_suggest_cloud_step_uses_injected_service(self):
        calls = []
        class Service:
            def execute_action(self, action_id, result, config):
                calls.append((action_id, result.data['title'], config['title']))
                return ActionResult(True, 'Todoist task created.', kind='cloud')
        effects = WorkflowEffects(integration_service=Service())
        outcome = WorkflowExecutor(SKILLS, effects.execute).execute(self.workflow,
            WorkflowContext.from_result(self.source))
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(calls, [('todoist_create_task', 'Homework', 'Do {title}')])

    def test_explicit_auto_cloud_workflow_is_valid(self):
        calls = []
        class Service:
            def execute_action(self, *args):
                calls.append(args[0])
                return ActionResult(True, 'Todoist task created.')
        workflow = replace(self.workflow, auto_run=True)
        outcome = WorkflowExecutor(SKILLS, WorkflowEffects(integration_service=Service()).execute).execute(
            workflow, WorkflowContext.from_result(self.source))
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(calls, ['todoist_create_task'])



if __name__ == '__main__':
    unittest.main()
