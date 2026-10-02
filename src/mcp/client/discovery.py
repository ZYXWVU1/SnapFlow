"""Bounded paginated discovery and non-networking JSON Schema validation."""
from copy import deepcopy
import hashlib
import json
from .models import CapabilitySnapshot, MCPToolDescriptor, valid_tool_name


def bounded_json(value, max_bytes=262144):
    size = 0
    chunks = []
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True).iterencode(value):
        size += len(chunk.encode('utf-8'))
        if size > max_bytes:
            raise ValueError('MCP payload exceeds its size limit.')
        chunks.append(chunk)
    return ''.join(chunks)


def validate_schema(schema):
    from jsonschema import Draft202012Validator
    if not isinstance(schema, dict):
        raise ValueError('Tool schema must be an object.')
    bounded_json(schema, 65536)
    schema_maps = {'properties', '$defs', 'definitions', 'dependentSchemas', 'dependencies'}
    schema_values = {'additionalProperties', 'unevaluatedProperties', 'propertyNames',
        'contains', 'not', 'if', 'then', 'else', 'items', 'additionalItems',
        'unevaluatedItems', 'contentSchema'}
    schema_lists = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
    def walk(value, depth=0, is_schema=True):
        if depth > 24:
            raise ValueError('Tool schema is too deeply nested.')
        if isinstance(value, dict):
            for key, item in value.items():
                if is_schema and key in ('pattern', 'patternProperties'):
                    raise ValueError('Untrusted regular-expression schemas are not supported in this milestone.')
                if is_schema and key in ('$ref', '$dynamicRef') and (not isinstance(item, str) or not item.startswith('#')):
                    raise ValueError('External schema references are not supported.')
                if is_schema and key in schema_maps and isinstance(item, dict):
                    # Map keys and annotation data are not schema keywords.
                    for child in item.values():
                        walk(child, depth + 2)
                elif is_schema and key in schema_lists and isinstance(item, list):
                    for child in item:
                        walk(child, depth + 2)
                else:
                    walk(item, depth + 1, is_schema and key in schema_values)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1, is_schema)
    walk(schema)
    Draft202012Validator.check_schema(schema)
    return deepcopy(schema)


async def _pages(method, field, limit):
    items, seen, cursor = [], set(), None
    for _ in range(16):
        result = await method(cursor=cursor, cache_mode='refresh')
        page = getattr(result, field)
        if len(page) + len(items) > limit:
            raise ValueError('Server capability count exceeds the limit.')
        items.extend(page)
        cursor = result.next_cursor
        if cursor is None:
            return items
        if cursor in seen:
            raise ValueError('Server repeated a discovery cursor.')
        seen.add(cursor)
    raise ValueError('Server discovery page limit reached.')


def _metadata(items, key):
    result, seen = [], set()
    for item in items:
        data = item.model_dump(mode='json')
        bounded_json(data, 65536)
        identity = data[key]
        if identity in seen:
            raise ValueError('Server returned duplicate capability identifiers.')
        seen.add(identity)
        result.append(data)
    return tuple(result)


async def discover(connection_id, client):
    caps = client.server_capabilities
    tools, resources, templates, prompts = (), (), (), ()
    if caps.tools is not None:
        descriptors, seen = [], set()
        for tool in await _pages(client.list_tools, 'tools', 256):
            valid_tool_name(tool.name)
            if tool.name in seen:
                raise ValueError('Server returned duplicate tool names.')
            seen.add(tool.name)
            schema = validate_schema(tool.input_schema)
            output = validate_schema(tool.output_schema) if tool.output_schema is not None else None
            title, description = (tool.title or tool.name)[:256], (tool.description or '')[:4000]
            fingerprint = hashlib.sha256(bounded_json({'input': schema, 'output': output,
                'title': title, 'description': description}, 140000).encode('utf-8')).hexdigest()
            descriptors.append(MCPToolDescriptor(connection_id, tool.name, title, description, schema, output, fingerprint))
        tools = tuple(descriptors)
    if caps.resources is not None:
        resources = _metadata(await _pages(client.list_resources, 'resources', 128), 'uri')
        templates = _metadata(await _pages(client.list_resource_templates, 'resource_templates', 128), 'uri_template')
    if caps.prompts is not None:
        prompts = _metadata(await _pages(client.list_prompts, 'prompts', 128), 'name')
        for prompt in prompts:
            valid_tool_name(prompt['name'])
    return CapabilitySnapshot(tools, resources, templates, prompts)
