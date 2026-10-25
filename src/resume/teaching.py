"""Build explicit user-confirmed category rules without copying typed values."""
from copy import deepcopy
import re
from uuid import uuid4

from .matching import ALIASES, SECTIONS, category_compatible, normalize
from .models import CATEGORIES, validate_rule, text


def build_rule(profile_id, field, category, entity_selector, scope, formatter='identity'):
    """Called only by the explicit Teach/Save path; this function does no I/O.

    The returned inert record can be previewed/validated before storage. Typing,
    editing after fill, and cancelling have no persistence side effects.
    """
    if not isinstance(field, dict) or not field.get('field_ref') or not re.fullmatch(r'[0-9a-f]{64}', field.get('descriptor_hash', '')):
        raise ValueError('Choose a current inventoried field before teaching')
    if category not in CATEGORIES:
        raise ValueError('Unknown category')
    # Never retain typed values, even if a caller supplied one with metadata.
    metadata = {key: deepcopy(field[key]) for key in ('role', 'name', 'section', 'group', 'input_type', 'autocomplete', 'options') if key in field}
    for key in ('name', 'section'):
        text(metadata.get(key, ''), 200, empty=key == 'section')
    if not category_compatible(category, metadata):
        raise ValueError('Category is incompatible with the field section or control')
    name = normalize(metadata['name'])
    if not name:
        raise ValueError('Teaching requires a bounded visible field label')
    aliases = list(ALIASES.get(category, ()))
    cues = list(dict.fromkeys([name] + aliases))[:12]
    kind = category.split('.')[0]
    sections = list(dict.fromkeys([normalize(metadata.get('section', ''))] + list(SECTIONS.get(kind, ()))))
    sections = [section for section in sections if section][:12]
    rule = dict(id=uuid4().hex, revision=0, profile_id=profile_id, category=category,
                entity_selector=deepcopy(entity_selector), field_cues=cues, section_cues=sections,
                control_kind=metadata.get('role'), scope=deepcopy(scope), formatter=formatter,
                enabled=True, confirmed_by_user=True)
    return validate_rule(rule)
