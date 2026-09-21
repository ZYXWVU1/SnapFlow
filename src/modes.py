"""Mode contracts and validation, independent of Qt and the AI provider."""
import json
import math
from dataclasses import dataclass
from typing import Any


DEBUG_SCHEMA = {
    'error_type': 'string', 'language': 'string', 'summary': 'string',
    'evidence': ['string'], 'root_cause': 'string',
    'fixes': [{'before': 'string', 'after': 'string', 'explanation': 'string'}],
    'confidence': 'low | medium | high',
}
EXTRACT_SCHEMAS = {
    'text': {'text': 'string'},
    'code': {'language': 'string', 'code': 'string'},
    'table': {'headers': ['string'], 'rows': [['JSON scalar']]},
    'receipt': {'merchant': 'string or null', 'date': 'string or null',
                'total': 'number or null', 'currency': 'string or null'},
    'contact': {'name': 'string or null', 'email': 'string or null', 'phone': 'string or null'},
    'event': {'title': 'string or null', 'date': 'string or null', 'time': 'string or null',
              'location': 'string or null', 'url': 'string or null'},
    'assignment': {'course': 'string or null', 'title': 'string or null',
                   'due_date': 'string or null', 'due_time': 'string or null'},
    'json': {'value': 'any valid JSON value'},
    'url': {'url': 'string'},
}


class ResponseFormatError(ValueError):
    """A structured answer cannot safely be presented as extracted facts."""


@dataclass(frozen=True)
class ModeResult:
    mode: str
    text: str
    data: dict[str, Any] | None = None


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError('Invalid structure')


def _strings(data: dict, fields: tuple | list, nullable: bool = False) -> None:
    for field in fields:
        _require(field in data and (isinstance(data[field], str) or (nullable and data[field] is None)))


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool, int)) or (isinstance(value, float) and math.isfinite(value))


def parse_result(mode: str, response: str) -> ModeResult:
    if mode not in ('debug', 'extract'):
        return ModeResult(mode, response)
    try:
        text = response.strip()
        if text.startswith('```') and text.endswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        data = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        _require(isinstance(data, dict))
        if mode == 'debug':
            _strings(data, ['error_type', 'language', 'summary', 'root_cause', 'confidence'])
            _require(data['confidence'] in ('low', 'medium', 'high'))
            _require(isinstance(data.get('evidence'), list) and all(isinstance(x, str) for x in data['evidence']))
            _require(isinstance(data.get('fixes'), list))
            for fix in data['fixes']:
                _require(isinstance(fix, dict))
                _strings(fix, ['before', 'after', 'explanation'])
            data = {key: data[key] for key in DEBUG_SCHEMA}
        else:
            kind = data.get('content_type')
            _require(isinstance(kind, str) and kind in EXTRACT_SCHEMAS)
            schema = EXTRACT_SCHEMAS[kind]
            # Some vision models echo the schema's type wrapper, e.g.
            # {"content_type":"code","code":{"language":...,"code":...}}.
            # Only unwrap an unambiguous envelope; validate every field below.
            if set(data) == {'content_type', kind} and isinstance(data.get(kind), dict):
                data = {**data[kind], 'content_type': kind}
            _require(all(key in data for key in schema))
            if kind == 'table':
                headers, rows = data['headers'], data['rows']
                _require(isinstance(headers, list) and bool(headers) and all(isinstance(x, str) for x in headers))
                _require(isinstance(rows, list))
                _require(all(isinstance(row, list) and len(row) == len(headers) and all(_scalar(x) for x in row) for row in rows))
            elif kind == 'receipt':
                _strings(data, ['merchant', 'date', 'currency'], nullable=True)
                total = data['total']
                _require(total is None or (type(total) in (float, int) and math.isfinite(total)))
            elif kind != 'json':
                _strings(data, list(schema), nullable=kind in ('contact', 'event', 'assignment'))
            data = {'content_type': kind, **{key: data[key] for key in schema}}
        # JSON exponent overflow can produce infinity even without NaN/Infinity
        # literals. Validate nested JSON values before handing them to the GUI.
        json.dumps(data, allow_nan=False)
        return ModeResult(mode, response, data)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
        raise ResponseFormatError(
            f'The AI returned an invalid {mode.title()} response. Retry this mode or switch to Ask.'
        ) from None
