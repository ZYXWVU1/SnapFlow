"""Schema, reference and privacy checks shared by storage, import and publication."""
from src.desktop_context.filters import redact, _CREDENTIAL
from .models import AutomationSkill, BrowserTargetTemplate
from .parameters import references, validate_inputs, bind, REFERENCE


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
    if skill.domain == 'browser':
        from src.browser.action_policy import SECRET
        if any(SECRET.search(value) for value in strings(data)):
            raise ValueError('Sensitive browser literal detected.')
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
        if isinstance(target, BrowserTargetTemplate):
            from src.browser.action_policy import SENSITIVE
            targets = (target, *(c.target for c in (*skill.preconditions, *skill.postconditions,
                *step.preconditions, *step.postconditions)))
            if any(SENSITIVE.search(t.name_template) or _CREDENTIAL.search(t.name_template) for t in targets):
                raise ValueError('Credential targets must remain manual without retained credential labels.')
            if step.executable:
                # Validate template shape with harmless typed witnesses. Runtime must
                # repeat the Gate 3 check with actual inputs and the current grant.
                samples = {}
                for spec in skill.inputs:
                    samples[spec.input_id] = (True if spec.value_type == 'boolean' else 1 if spec.value_type == 'number' else
                        spec.enum_values[0] if spec.enum_values else 'reviewed input')
                refs = references(step.value_template)
                if refs and step.action_type in ('browser_set_checked', 'browser_set_text', 'browser_select_option'):
                    match = REFERENCE.fullmatch(step.value_template) if isinstance(step.value_template, str) else None
                    kinds = {i.input_id: i.value_type for i in skill.inputs}
                    if step.action_type == 'browser_set_checked' and (not match or kinds[match.group(1)] != 'boolean'):
                        raise ValueError('Checkbox values require a boolean input reference.')
                    if step.action_type != 'browser_set_checked' and any(kinds[key] not in ('string', 'text', 'enum', 'date', 'time') for key in refs):
                        raise ValueError('Browser text and selection values require text inputs.')
                elif refs:
                    raise ValueError('Only browser field values may be parameterized.')
                target.spec(step.action_type, step.value_template, lambda value: bind(value, samples))
        else:
            if _CREDENTIAL.search(' '.join((target.name_template or '', target.automation_id or '', *target.ancestor_templates))):
                raise ValueError('Credential targets are unsupported.')
            if skill.supported_apps and target.process_name.casefold() not in {p.casefold() for p in skill.supported_apps}:
                raise ValueError('A step targets an application outside the declared skill scope.')
        if step.risk_level in ('ui_sensitive', 'ui_destructive'):
            raise ValueError('Sensitive/destructive steps are unsupported.')
        if publish and (not step.reviewed or step.action_type == 'invoke' and not step.postconditions):
            raise ValueError('Review every step and add an outcome condition to each invocation.')
    return skill
