"""JSON-only step references, never expressions or attribute access."""
from collections.abc import Mapping
import re
from .models import thaw

_REFERENCE = re.compile(r'\$\{(?:step\.)?([A-Za-z0-9_-]{1,128})\.([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)\}')


def references(value):
    if isinstance(value, Mapping):
        return [ref for child in value.values() for ref in references(child)]
    if isinstance(value, (list, tuple)):
        return [ref for child in value for ref in references(child)]
    if not isinstance(value, str):
        return []
    matches = list(_REFERENCE.finditer(value))
    if '${' in _REFERENCE.sub('', value) or any(part.startswith('_') for m in matches for part in m.group(2).split('.')):
        raise ValueError('Unsupported Agent input binding.')
    return [(m.group(1), tuple(m.group(2).split('.'))) for m in matches]


def resolve_bindings(value, outputs):
    references(value)
    if isinstance(value, Mapping):
        return {k: resolve_bindings(v, outputs) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve_bindings(v, outputs) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(match):
        result = outputs.get(match.group(1))
        for key in match.group(2).split('.'):
            if not isinstance(result, Mapping) or key not in result:
                raise ValueError('Referenced step output is unavailable.')
            result = result[key]
        return thaw(result)

    match = _REFERENCE.fullmatch(value)
    if match:
        return lookup(match)
    def stringify(match):
        result = lookup(match)
        if result is None or isinstance(result, (dict, list)):
            raise ValueError('Structured values require a complete binding.')
        return str(result)
    return _REFERENCE.sub(stringify, value)
