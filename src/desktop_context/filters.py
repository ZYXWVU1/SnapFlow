"""Bound text and remove likely secrets before creating any AI source."""
from dataclasses import replace
import re

_CREDENTIAL = re.compile(r'password|passphrase|credential|api[ _-]?key|access[ _-]?token|secret|pin\b|one[ -]?time[ -]?code', re.I)
_PRIVATE_KEY = re.compile(r'-----BEGIN [^\n]*PRIVATE KEY-----.*?(?:-----END [^\n]*PRIVATE KEY-----|\Z)', re.S)
_SECRET = re.compile(r'(?i)\b(?:sk-[a-z0-9_-]{20,}|gh[pousr]_[a-z0-9_]{20,}|AKIA[A-Z0-9]{16}|eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)\b')
_ASSIGNMENT = re.compile(r'(?i)\b(password|passphrase|api[ _-]?key|secret|(?:access|refresh|auth)?[ _-]?token|authorization|cookie)\b["\s]*[:=]\s*(?:Bearer\s+)?(?:"[^"\n]*"|\S+)')


def redact(value, limit=3000):
    if not isinstance(value, str):
        return ''
    # Apply matching before truncation so a truncated private-key header cannot escape.
    value = _PRIVATE_KEY.sub('[REDACTED]', value[:65536])
    value = _ASSIGNMENT.sub(lambda m: m.group(1) + ': [REDACTED]', value)
    value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [REDACTED]', value)
    value = _SECRET.sub('[REDACTED]', value)
    return ''.join(c for c in value if c >= ' ' or c in '\n\t')[:limit]


def sensitive(element):
    return element.protected or bool(_CREDENTIAL.search(' '.join((
        element.name or '', element.automation_id or '', element.control_type or ''))))


def filter_elements(elements):
    result = []
    remaining = 9000
    for element in tuple(elements)[:300]:
        if sensitive(element) or element.depth > 6 or remaining <= 0:
            continue
        name = redact(element.name, min(500, remaining))
        remaining -= len(name)
        value = redact(element.value, min(1500, remaining))
        remaining -= len(value)
        result.append(replace(element, name=name or None, value=value or None,
            automation_id=redact(element.automation_id, 128) or None,
            class_name=redact(element.class_name, 128) or None))
    return tuple(result)
