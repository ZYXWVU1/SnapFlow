"""Closed, non-generative adapters for reviewed resume facts."""
from datetime import date
import re


FORMATTERS = frozenset({'identity', 'reviewed_full_name', 'list_join', 'date', 'month', 'option_alias'})


def _date_value(value, precision):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', value):
        raise ValueError('date requires existing month/day precision')
    year, month, *day = map(int, value.split('-'))
    try:
        date(year, month, day[0] if day else 1)
    except ValueError as exc:
        raise ValueError('invalid date') from exc
    if precision == 'date' and not day:
        raise ValueError('date requires a reviewed day')
    return value[:7] if precision == 'month' else value


def format_value(value, field, formatter='identity'):
    """Return an exact supported value or raise; never truncate or infer precision.

    ``option_alias`` accepts an explicitly reviewed alias bundle as ``value``;
    ordinary select values must match the visible option label exactly.
    """
    if formatter not in FORMATTERS:
        raise ValueError('unsupported formatter')
    if formatter == 'option_alias':
        if isinstance(value, dict) and value.get('reviewed') is True:
            aliases = value.get('option_aliases', {})
            raw = value.get('value')
            if not isinstance(aliases, dict) or raw not in aliases:
                raise ValueError('missing reviewed option alias')
            value = aliases[raw]
        elif not isinstance(value, str):
            raise ValueError('missing reviewed option alias')
    if formatter == 'list_join':
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            raise ValueError('list formatter requires reviewed text items')
        value = ', '.join(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('missing reviewed text value')
    if any(ord(char) < 32 and char not in '\n\t\r' for char in value):
        raise ValueError('unsupported control character')
    input_type = field.get('input_type', 'text')
    if formatter in {'date', 'month'}:
        value = _date_value(value, formatter)
    if input_type in {'date', 'month'}:
        value = _date_value(value, input_type)
    if input_type == 'number' and not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
        raise ValueError('number requires a reviewed numeric value')
    options = field.get('options', [])
    if field.get('role') in {'combobox', 'listbox'} or options:
        if not isinstance(options, list) or value not in options:
            raise ValueError('no exact reviewed select option')
    limit = field.get('max_length')
    if len(value) > 1000 or (isinstance(limit, int) and limit >= 0 and len(value) > limit):
        raise ValueError('value exceeds field/action length; manual entry required')
    return value
