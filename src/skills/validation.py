"""Conservative normalization of untrusted extracted fields."""
from datetime import date, datetime
import math
import re
from urllib.parse import urlsplit


def text(value):
    return value.strip() or None if isinstance(value, str) else None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def positive_integer(value):
    return value if type(value) is int and value > 0 else None


def date_value(value):
    value = text(value)
    if not value or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def time_value(value):
    value = text(value)
    if value:
        for fmt in ('%H:%M', '%I:%M %p', '%I %p'):
            try:
                return datetime.strptime(value.upper(), fmt).strftime('%H:%M')
            except ValueError:
                pass
    return None


def url(value):
    value = text(value)
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def string_list(value):
    return [s for item in value if (s := text(item))] if isinstance(value, list) else []


def fields(raw, schema, validators=None):
    validators = validators or {}
    data, warnings = {}, []
    for key in schema:
        value = raw.get(key)
        data[key] = validators.get(key, text)(value)
        if value is not None and data[key] is None:
            warnings.append(f'{key.replace("_", " ").capitalize()} could not be reliably read.')
    return data, warnings
