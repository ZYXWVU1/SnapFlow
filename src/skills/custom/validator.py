"""Conservative typed normalization; missing required data produces warnings."""
from datetime import datetime
import math
import re
from src.skills.validation import date_value, url


def normalize_value(kind, value):
    if value is None:
        return None
    if kind in ('string', 'multiline_text'):
        return value if isinstance(value, str) and value.strip() else None
    if kind == 'number':
        return value if type(value) in (int, float) and math.isfinite(value) else None
    if kind == 'boolean':
        return value if type(value) is bool else None
    if kind == 'list_string':
        return value if isinstance(value, list) and all(isinstance(v, str) for v in value) else None
    if not isinstance(value, str):
        return None
    if kind == 'date':
        return date_value(value)
    if kind == 'time':
        return value if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) else None
    if kind == 'datetime':
        if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value):
            return None
        try:
            datetime.fromisoformat(value)
            return value
        except ValueError:
            return None
    if kind == 'url':
        return url(value)
    if kind == 'email':
        return value if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) else None
    return None


def validate_extraction(definition, raw):
    data, warnings = {}, []
    for field in definition.fields:
        original = raw.get(field.id)
        value = normalize_value(field.field_type, original)
        data[field.id] = value
        if field.required and (value is None or value == []):
            warnings.append(f'Required field "{field.label}" was not detected.')
        elif original is not None and value is None:
            warnings.append(f'"{field.label}" had an invalid {field.field_type} value and was omitted.')
    return data, warnings
