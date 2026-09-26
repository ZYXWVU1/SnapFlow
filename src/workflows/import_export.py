"""Definition-only .aiworkflow interchange; every import requires review."""
from dataclasses import replace
import json
from src.skill_actions import ACTIONS
from .models import WorkflowDefinition
from .validator import validate_workflow

FORMAT = 'ai-screenshot-helper-workflow'


def export_workflow(definition):
    return json.dumps({'format': FORMAT, 'version': 1, 'workflow': definition.to_dict()},
                      ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def import_workflow(source, existing=(), skills=None):
    if not isinstance(source, str) or len(source) > 1_000_000:
        raise ValueError('Workflow file must be under 1 MB.')
    try:
        raw = json.loads(source)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Invalid Workflow JSON.') from exc
    if not isinstance(raw, dict) or raw.get('format') != FORMAT or type(raw.get('version')) is not int or raw['version'] != 1:
        raise ValueError('Unsupported Workflow format or version.')
    definition = WorkflowDefinition.from_dict(raw.get('workflow'))
    warnings = ['Imported Workflow is disabled and set to Suggest until reviewed.']
    identifier = definition.id
    suffix = 2
    while identifier in existing:
        identifier = definition.id[:110] + '_' + str(suffix)
        suffix += 1
    if identifier != definition.id:
        warnings.append('Duplicate ID imported as a copy.')
    definition = replace(definition, id=identifier, enabled=False, auto_run=False)
    for step in definition.steps:
        if step.action_id not in ACTIONS:
            warnings.append('Unknown Action: ' + step.action_id)
    if skills is not None:
        skill = skills.get(definition.trigger.skill_id)
        if skill is None:
            warnings.append('Missing or disabled Skill: ' + definition.trigger.skill_id)
        elif validate_workflow(definition, skills):
            warnings.append('Review conditions and Action configuration before enabling.')
    return definition, warnings
