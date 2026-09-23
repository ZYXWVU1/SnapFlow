"""Definition-only interchange. Duplicate imports become copies."""
from dataclasses import replace
import json
from .json_response import json_object
from .models import CUSTOM_ACTIONS, CustomSkillDefinition, unique_id

FORMAT = 'ai-screenshot-helper-skill'


def export_skill(definition):
    return json.dumps(dict(format=FORMAT, version=1, skill=definition.to_dict()), ensure_ascii=False, indent=2) + '\n'


def import_skill(source, existing=()):
    raw = json_object(source)
    if raw.get('format') != FORMAT or type(raw.get('version')) is not int or raw['version'] != 1:
        raise ValueError('Unsupported skill file format or version.')
    definition = raw.get('skill')
    if not isinstance(definition, dict) or not isinstance(definition.get('actions', []), list):
        raise ValueError('Invalid skill definition.')
    warnings = []
    actions = definition.get('actions', [])
    filtered = [a for a in actions if isinstance(a, str) and a in CUSTOM_ACTIONS]
    if filtered != actions:
        warnings.append('Unsupported actions were removed.')
    skill = CustomSkillDefinition.from_dict(definition | {'actions': filtered})
    new_id = unique_id(skill.id, existing)
    if new_id != skill.id:
        skill = replace(skill, id=new_id, name=(skill.name[:93] + ' (copy)'))
        warnings.append('A duplicate ID was imported as a copy.')
    return skill, warnings
