"""Bounded, non-sensitive SDK elicitation requests; no automatic answers."""
import re
from .discovery import bounded_json, validate_schema
from .oauth import browser_url


def sensitive_text(value):
    text = re.sub('[^a-z0-9]', '', str(value).lower())
    return any(word in text for word in ('password', 'secret', 'token', 'credential', 'apikey',
        'accesskey', 'creditcard', 'cardnumber', 'socialsecurity'))


def normalize_request(connection_id, operation, target, params):
    if len(params.message.encode()) > 4000:
        raise ValueError('Interaction message exceeds its limit.')
    request = {'connection_id': connection_id, 'operation': operation, 'target': target,
        'mode': params.mode, 'message': params.message}
    if params.mode == 'url':
        request['url'] = browser_url(params.url)
    elif params.mode == 'form':
        schema = validate_schema(params.requested_schema)
        if sensitive_text(params.message + ' ' + str(schema.get('title', '')) +
                ' ' + str(schema.get('description', ''))):
            raise ValueError('Sensitive values require out-of-band browser authorization.')
        bounded_json(schema, 16384)
        fields = schema.get('properties', {})
        if schema.get('type') != 'object' or not fields or len(fields) > 16:
            raise ValueError('Unsupported interaction form.')
        for name, field in fields.items():
            if (sensitive_text(name + ' ' + str(field.get('title', '')) + ' ' + str(field.get('description', ''))) or
                    field.get('format') == 'password' or field.get('writeOnly')):
                raise ValueError('Sensitive values require out-of-band browser authorization.')
            kind = field.get('type', 'string' if 'enum' in field else None)
            if kind not in ('string', 'integer', 'number', 'boolean', 'array'):
                raise ValueError('Only primitive interaction fields are supported.')
            if kind == 'array' and (field.get('items', {}).get('type') != 'string'):
                raise ValueError('Only string arrays are supported in interaction forms.')
            if any(key in field for key in ('$ref', '$dynamicRef', 'anyOf', 'oneOf', 'allOf')):
                raise ValueError('Unsupported interaction field schema.')
        request['schema'] = schema
    else:
        raise ValueError('Unsupported interaction mode.')
    return request


def validate_response(request, response):
    from mcp.types import ElicitResult
    from jsonschema import Draft202012Validator
    if not isinstance(response, dict) or response.get('action') not in ('accept', 'decline', 'cancel'):
        return ElicitResult(action='cancel')
    if response['action'] != 'accept':
        return ElicitResult(action=response['action'])
    if request['mode'] == 'url':
        return ElicitResult(action='accept')
    content = response.get('content')
    try:
        bounded_json(content, 16384)
        if not isinstance(content, dict) or set(content) - set(request['schema']['properties']):
            raise ValueError('Unexpected response fields.')
        Draft202012Validator(request['schema']).validate(content)
        return ElicitResult(action='accept', content=content)
    except Exception:
        return ElicitResult(action='cancel')
