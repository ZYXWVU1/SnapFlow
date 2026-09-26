"""Adapt existing custom and built-in Skill fields to the review editor."""
from types import SimpleNamespace

from src.skills.custom.models import CustomFieldDefinition
from src.skills.registry import SKILLS


def editable_schema(skill_id, custom_storage):
    if custom_storage is not None:
        definition = custom_storage.get_skill(skill_id)
        if definition is not None:
            return definition
    skill = SKILLS.get(skill_id)
    if skill is None or skill_id == 'table':
        return None
    fields = []
    for key, value in skill.schema.items():
        if isinstance(value, list):
            if value == ['string']:
                kind = 'list_string'
            else:
                continue
        elif isinstance(value, str):
            kind = 'number' if value.startswith(('number', 'positive integer')) else 'string'
            if key.endswith('_date') or key == 'date':
                kind = 'date'
            elif key.endswith('_time'):
                kind = 'time'
            elif key.endswith('url'):
                kind = 'url'
            elif key in ('instructions_summary', 'description', 'error_message', 'likely_cause'):
                kind = 'multiline_text'
        else:
            continue
        fields.append(CustomFieldDefinition(key, key.replace('_', ' ').title(), kind))
    return SimpleNamespace(fields=tuple(fields)) if fields else None
