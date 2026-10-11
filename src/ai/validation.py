"""Formal response schemas complement, and never replace, existing Skill validators."""
import json
from jsonschema import Draft202012Validator


def schema_validator(schema):
    try:
        if not isinstance(schema, dict):
            raise ValueError()
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except Exception:
        raise ValueError('The requested response schema is invalid.') from None


def validate_schema(schema, content):
    from src.skills.custom.json_response import json_object
    validator = schema_validator(schema)
    try:
        if not isinstance(content, str) or len(content) > 1000000:
            raise ValueError()
        data = json_object(content)
        json.dumps(data, allow_nan=False)
        validator.validate(data)
        return data
    except Exception:
        raise ValueError('AI output did not match the requested schema. Retry or choose a compatible model.') from None
