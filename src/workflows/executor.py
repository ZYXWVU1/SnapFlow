"""Sequential execution; injected effect boundary keeps Qt outside the engine."""
from copy import deepcopy
from datetime import datetime, timezone
import uuid
from src.skill_actions import ACTIONS, ActionResult
from .conditions import evaluate_conditions
from .models import WorkflowExecutionResult, WorkflowStepResult
from .validator import WORKFLOW_ACTIONS, validate_workflow


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkflowExecutor:
    def __init__(self, skills, execute_action=None, actions=None):
        self.skills = skills
        self.actions = ACTIONS if actions is None else actions
        self.execute_action = execute_action

    def execute(self, workflow, context, *, cancel=None, preview=False):
        workflow = deepcopy(workflow)
        result = WorkflowExecutionResult(workflow.id, 'running', now(),
            [WorkflowStepResult(s.id, s.action_id) for s in workflow.steps],
            uuid.uuid4().hex, context.request_id, preview=preview)

        def finish(status, error=None):
            result.status, result.error, result.finished_at = status, error, now()
            for step in result.steps:
                if step.status == 'pending':
                    step.status = 'skipped'
            return result

        if cancel is not None and cancel.is_set():
            return finish('cancelled')
        if not workflow.enabled and not preview:
            return finish('skipped')
        errors = validate_workflow(workflow, self.skills, self.actions)
        if errors:
            return finish('failed', ' '.join(errors))
        if context.skill_id != workflow.trigger.skill_id:
            return finish('skipped', 'Trigger Skill does not match.')
        if not evaluate_conditions(workflow.conditions, context.data):
            return finish('skipped', 'Conditions did not match.')
        for step, outcome in zip(workflow.steps, result.steps):
            if cancel is not None and cancel.is_set():
                return finish('cancelled')
            if not step.enabled:
                outcome.status = 'skipped'
                continue
            outcome.status = 'running'
            action = self.actions[step.action_id]
            try:
                source = context.result
                if (action.id not in source.actions and action.id not in WORKFLOW_ACTIONS) or not action.enabled(source):
                    response = ActionResult(False, 'Required information or action availability is missing.')
                elif preview:
                    outcome.preview = action.preview(source, deepcopy(step.config))
                    response = ActionResult(True, 'Preview only; no action executed.')
                elif self.execute_action is None:
                    response = ActionResult(False, 'No action execution adapter is configured.')
                else:
                    response = self.execute_action(action, source, deepcopy(step.config))
                if not isinstance(response, ActionResult):
                    raise TypeError('Invalid action result.')
                outcome.status = 'success' if response.success else 'failed'
                outcome.message = response.message
                if not response.success:
                    outcome.error = response.message or 'Action failed.'
            except Exception as exc:
                outcome.status = 'failed'
                outcome.error = f'Action failed ({type(exc).__name__}).'
            if outcome.status == 'failed' and workflow.failure_policy == 'stop':
                return finish('failed')
        failed = any(s.status == 'failed' for s in result.steps)
        succeeded = any(s.status == 'success' for s in result.steps)
        return finish('partial_success' if failed and succeeded else 'failed' if failed else 'success')
