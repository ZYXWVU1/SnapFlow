"""Schema, reference and privacy checks shared by storage, import and publication."""
from src.desktop_context.filters import redact, _CREDENTIAL
from .models import AutomationSkill
from .parameters import references, validate_inputs


def validate_definition(skill, *, publish=False):
    if not isinstance(skill, AutomationSkill):
        raise ValueError('A typed Automation Skill is required.')
    data = skill.to_dict()
    def strings(value):
        if isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
        elif isinstance(value, str):
            yield value
    if any(redact(v, 65536) != v for v in strings(data)):
        raise ValueError('Sensitive literal detected. Remove it before saving this skill.')
    declared = {i.input_id for i in skill.inputs}
    if not references(data) <= declared:
        raise ValueError('Skill contains an undeclared input or unsupported reference.')
    for spec in skill.inputs:
        if _CREDENTIAL.search(spec.label + ' ' + spec.input_id):
            raise ValueError('Credential inputs are unsupported.')
        if spec.default_value is not None:
            validate_inputs((spec,), {spec.input_id: spec.default_value})
    for step in skill.steps:
        target = step.target_template
        if _CREDENTIAL.search(' '.join((target.name_template or '', target.automation_id or '', *target.ancestor_templates))):
            raise ValueError('Credential targets are unsupported.')
        if skill.supported_apps and target.process_name.casefold() not in {p.casefold() for p in skill.supported_apps}:
            raise ValueError('A step targets an application outside the declared skill scope.')
        if step.risk_level in ('ui_sensitive', 'ui_destructive'):
            raise ValueError('Sensitive/destructive steps are unsupported.')
        if publish and (not step.reviewed or step.action_type == 'invoke' and not step.postconditions):
            raise ValueError('Review every step and add an outcome condition to each invocation.')
    return skill
