"""Literal input references and user-confirmed parameter choices, never expressions."""
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
import math
import re

from src.agent.models import thaw
from src.desktop_context.filters import redact
from .models import AutomationInput, identifier

REFERENCE = re.compile(r'\$\{input\.([A-Za-z][A-Za-z0-9_-]{0,63})\}')


def references(value):
    if isinstance(value, Mapping):
        return set().union(*(references(v) for v in value.values())) if value else set()
    if isinstance(value, (tuple, list)):
        return set().union(*(references(v) for v in value)) if value else set()
    if not isinstance(value, str):
        return set()
    if '${' in REFERENCE.sub('', value):
        raise ValueError('Only ${input.name} references are supported; expressions and implicit context are disabled.')
    return {m.group(1) for m in REFERENCE.finditer(value)}


def bind(value, inputs):
    refs = references(value)
    if not refs <= set(inputs):
        raise ValueError('A required input reference is unavailable.')
    if isinstance(value, Mapping):
        return {k: bind(v, inputs) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [bind(v, inputs) for v in value]
    if not isinstance(value, str):
        return value
    match = REFERENCE.fullmatch(value)
    if match:
        return thaw(inputs[match.group(1)])
    return REFERENCE.sub(lambda m: str(inputs[m.group(1)]), value)


def validate_inputs(schema, values):
    if not isinstance(values, Mapping) or set(values) - {i.input_id for i in schema}:
        raise ValueError('Supply only declared inputs.')
    result = {}
    for spec in schema:
        value = values.get(spec.input_id, thaw(spec.default_value))
        if value is None:
            if spec.required:
                raise ValueError('A required input is missing: ' + spec.label)
            continue
        kind = spec.value_type
        if kind == 'number':
            valid = type(value) in (int, float) and math.isfinite(value)
        elif kind == 'boolean':
            valid = type(value) is bool
        else:
            valid = type(value) is str and len(value) <= spec.max_length and (not spec.required or bool(value.strip()))
            if valid and kind == 'enum':
                valid = value in spec.enum_values
            if valid and kind in ('date', 'time'):
                try:
                    (date if kind == 'date' else time).fromisoformat(value)
                except ValueError:
                    valid = False
        if not valid or isinstance(value, str) and redact(value, 65536) != value:
            raise ValueError('Input has an invalid type, format or sensitive value: ' + spec.label)
        result[spec.input_id] = value
    return result


def candidates(skill):
    return tuple({'step_id': step.step_id, 'label': step.title, 'value': thaw(step.value_template),
        'confidence': .9, 'value_type': 'boolean' if step.action_type == 'toggle' else
        'number' if step.action_type == 'set_range_value' else 'string'}
        for step in skill.steps if step.action_type in ('set_value', 'set_range_value', 'toggle') and not references(step.value_template))


def parameterize(skill, step_id, input_id, label, *, value_type=None, enum_values=(), target_name=False):
    identifier(input_id)
    if any(i.input_id == input_id for i in skill.inputs):
        raise ValueError('Input identifiers must be unique.')
    selected = next((s for s in skill.steps if s.step_id == step_id), None)
    if selected is None or selected.action_type not in ('set_value', 'set_range_value', 'toggle', 'select'):
        raise ValueError('Choose a value/selection step to parameterize.')
    value_type = value_type or ('boolean' if selected.action_type == 'toggle' else
        'number' if selected.action_type == 'set_range_value' else 'enum' if enum_values else 'string')
    spec = AutomationInput(input_id, label, value_type, enum_values=enum_values)
    reference = '${input.' + input_id + '}'
    steps = []
    for step in skill.steps:
        if step.step_id != step_id:
            steps.append(step)
            continue
        target = replace(step.target_template, name_template=reference,
            automation_id=None if step.action_type == 'select' else step.target_template.automation_id) if target_name or step.action_type == 'select' else step.target_template
        # Replace evidence literals in verification as well as the action value.
        def conditions(items):
            return tuple(replace(c, equals=reference if c.equals == step.value_template else c.equals,
                target=target if c.target == step.target_template else c.target) for c in items)
        steps.append(replace(step, title=label, target_template=target,
            value_template=None if step.action_type == 'select' else reference,
            preconditions=(), postconditions=conditions(step.postconditions), reviewed=False))
    return replace(skill, steps=tuple(steps), inputs=(*skill.inputs, spec), status='draft')
