"""Resolve dependencies dynamically; preserve invalid stored definitions for repair."""
from src.skill_actions import ACTIONS
from .models import WorkflowDefinition
from .conditions import is_number
import re

WORKFLOW_ACTIONS = frozenset({'append_csv', 'save_csv', 'show_notification',
    'google_calendar_create_event', 'google_sheets_append_row', 'todoist_create_task'})
TEMPLATE_FIELD = re.compile(r'\{([a-z][a-z0-9_]*)\}')


def skill_fields(skill):
    if hasattr(skill, 'definition'):
        return {f.id: f.field_type for f in skill.definition.fields}
    return dict(skill.schema)


def field_kind(schema):
    if isinstance(schema, list) or schema == 'list_string':
        return 'list'
    if isinstance(schema, dict):
        return 'object'
    if 'number' in schema or 'integer' in schema:
        return 'number'
    if schema == 'boolean':
        return 'boolean'
    return 'string'


def compatible_value(kind, value):
    if value is None:
        return True
    if kind == 'number':
        return is_number(value)
    return isinstance(value, {'list': list, 'object': dict, 'string': str, 'boolean': bool}[kind])


def validate_workflow(workflow, skills, actions=None):
    actions = ACTIONS if actions is None else actions
    try:
        WorkflowDefinition.from_dict(workflow.to_dict())
    except (ValueError, AttributeError):
        return ['Invalid workflow definition.']
    skill = skills.get(workflow.trigger.skill_id)
    if skill is None:
        return ['Trigger Skill is missing or disabled.']
    errors = []
    fields = skill_fields(skill)
    for condition in workflow.conditions:
        if condition.field not in fields:
            errors.append(f'Unknown condition field: {condition.field}.')
            continue
        kind = field_kind(fields[condition.field])
        numeric = kind == 'number'
        if condition.operator in ('greater_than', 'less_than') and (not numeric or not is_number(condition.value)):
            errors.append('Numeric conditions require a numeric field and value.')
        if condition.operator in ('contains', 'not_contains'):
            if kind != 'string' or not isinstance(condition.value, str):
                errors.append('Contains requires a text field and value.')
        if condition.operator in ('equals', 'not_equals') and not compatible_value(kind, condition.value):
            errors.append('Condition value does not match the field type.')
    for step in workflow.steps:
        action = actions.get(step.action_id)
        if action is None:
            errors.append(f'Unknown action: {step.action_id}.')
            continue
        if action.id not in skill.action_ids and action.id not in WORKFLOW_ACTIONS:
            errors.append(f'Action is not available for this Skill: {action.id}.')
        if action.integration_id:
            from src.integrations.actions import validate_config as validate_external_config
            errors.extend(validate_external_config(action.id, step.config, set(fields)))
        else:
            errors.extend(action.validate_config(step.config))
        if action.id == 'show_notification':
            for key in ('title', 'message'):
                template = step.config.get(key)
                if isinstance(template, str):
                    remainder = TEMPLATE_FIELD.sub('', template)
                    if '{' in remainder or '}' in remainder:
                        errors.append('Only simple {field_name} placeholders are supported.')
                    for field in TEMPLATE_FIELD.findall(template):
                        if field not in fields:
                            errors.append(f'Unknown template field: {field}.')
        if workflow.auto_run and action.risk_level not in ('read_only', 'clipboard', 'local_write', 'external_write'):
            errors.append('This action requires manual execution.')
    return errors
