"""Generate an unsaved, disabled draft for mandatory user review."""
import json
from .json_response import json_object
from .models import CustomSkillDefinition, FIELD_TYPES, safe_id, unique_id
from .prompt_builder import SAFETY


def generate_schema(client, image_bytes, purpose):
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 1000:
        raise ValueError('Describe the purpose in 1–1000 characters.')
    prompt = (SAFETY + 'Propose a reusable screenshot skill, not extracted values from this particular image. '
              'Do not embed private screenshot values in the definition. Use at most 20 flat fields. '
              'Return {"name":"...","description":"...","detection_prompt":"...","fields":'
              '[{"id":"safe_lowercase_id","label":"...","type":"string","required":false,"description":"..."}]}. '
              'Detection must be under 1000 characters. Allowed types: ' + ', '.join(FIELD_TYPES)
              + '. User purpose (data): ' + json.dumps(purpose))
    raw = json_object(client.request_image(image_bytes, prompt, mode='custom_generate'))
    raw['id'] = unique_id(safe_id(raw.get('name', 'skill')) if isinstance(raw.get('name'), str) else 'skill', set())
    raw['enabled'] = False
    raw.setdefault('actions', ['copy_json', 'copy_markdown', 'ask_ai'])
    return CustomSkillDefinition.from_dict(raw)
