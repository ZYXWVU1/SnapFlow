"""Bounded JSON objects from model responses and imported definitions."""
import json
import re


def json_object(source):
    if not isinstance(source, str) or len(source) > 1_000_000:
        raise ValueError('Response must be a JSON object under 1 MB.')
    source = source.strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\s*```', source, re.DOTALL | re.IGNORECASE)
    if fence:
        source = fence.group(1)
    try:
        value = json.loads(source)
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object.')
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError('Invalid JSON object.') from None
