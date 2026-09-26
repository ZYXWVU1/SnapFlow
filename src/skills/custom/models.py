"""Strict, versioned flat schemas shared by editing, storage and AI drafts."""
from dataclasses import dataclass
import re
import unicodedata

FIELD_TYPES = ('string', 'multiline_text', 'number', 'date', 'time', 'datetime', 'boolean', 'url', 'email', 'list_string')
CUSTOM_ACTIONS = ('copy_json', 'copy_markdown', 'copy_text', 'save_csv', 'ask_ai')
BUILTIN_IDS = frozenset({'assignment', 'event', 'code_error', 'table'})
MAX_FIELDS = 20
MAX_DETECTION_LENGTH = 1000


def safe_id(label):
    value = unicodedata.normalize('NFKD', label).encode('ascii', 'ignore').decode().lower()
    value = re.sub(r'[^a-z0-9]+', '_', value).strip('_')[:56] or 'field'
    return value if value[0].isalpha() else 'field_' + value


def unique_id(base, existing):
    candidate, suffix = base, 2
    while candidate in existing or candidate in BUILTIN_IDS:
        candidate = f'{base[:56]}_{suffix}'
        suffix += 1
    return candidate


def check_text(value, label, maximum, required=True):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise ValueError(f'{label} must be {"non-empty " if required else ""}text, at most {maximum} characters.')


def check_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value):
        raise ValueError('IDs must start with a lowercase letter and contain only lowercase letters, numbers and underscores (max 64).')


@dataclass(frozen=True)
class CustomFieldDefinition:
    id: str
    label: str
    field_type: str
    required: bool = False
    description: str | None = None

    def __post_init__(self):
        check_id(self.id)
        check_text(self.label, 'Field label', 100)
        if self.field_type not in FIELD_TYPES:
            raise ValueError('Unsupported field type.')
        if type(self.required) is not bool:
            raise ValueError('Required must be true or false.')
        if self.description is not None:
            check_text(self.description, 'Field description', 1000, False)

    def to_dict(self):
        return dict(id=self.id, label=self.label, type=self.field_type, required=self.required, description=self.description)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError('Each field must be an object.')
        try:
            return cls(raw['id'], raw['label'], raw['type'], raw.get('required', False), raw.get('description'))
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid field definition.') from exc


@dataclass(frozen=True)
class CustomSkillDefinition:
    id: str
    name: str
    description: str
    detection_prompt: str
    fields: tuple[CustomFieldDefinition, ...]
    actions: tuple[str, ...]
    enabled: bool = True
    version: int = 1
    extraction_prompt: str = ''
    few_shot_examples: tuple[dict, ...] = ()

    def __post_init__(self):
        check_id(self.id)
        if self.id in BUILTIN_IDS:
            raise ValueError('This ID is reserved for a built-in skill.')
        check_text(self.name, 'Skill name', 100)
        check_text(self.description, 'Description', 1000, False)
        check_text(self.detection_prompt, 'Detection condition', MAX_DETECTION_LENGTH)
        if not isinstance(self.fields, (list, tuple)) or not 1 <= len(self.fields) <= MAX_FIELDS:
            raise ValueError('Define between 1 and 20 fields.')
        if not all(isinstance(f, CustomFieldDefinition) for f in self.fields):
            raise ValueError('Invalid field definition.')
        if len({f.id for f in self.fields}) != len(self.fields):
            raise ValueError('Field IDs must be unique.')
        if not isinstance(self.actions, (list, tuple)) or any(not isinstance(a, str) or a not in CUSTOM_ACTIONS for a in self.actions):
            raise ValueError('Unsupported action.')
        if len(set(self.actions)) != len(self.actions):
            raise ValueError('Actions must be unique.')
        if type(self.enabled) is not bool or type(self.version) is not int or self.version != 1:
            raise ValueError('Unsupported skill version or invalid enabled flag.')
        check_text(self.extraction_prompt, 'Extraction guidance', 4000, False)
        if (not isinstance(self.few_shot_examples, (list, tuple)) or len(self.few_shot_examples) > 5
                or any(not isinstance(example, dict) for example in self.few_shot_examples)):
            raise ValueError('Use at most five example objects.')
        object.__setattr__(self, 'fields', tuple(self.fields))
        object.__setattr__(self, 'actions', tuple(self.actions))
        object.__setattr__(self, 'few_shot_examples', tuple(self.few_shot_examples))

    def to_dict(self):
        return dict(id=self.id, name=self.name, description=self.description, detection_prompt=self.detection_prompt,
                    fields=[f.to_dict() for f in self.fields], actions=list(self.actions), enabled=self.enabled, version=self.version,
                    extraction_prompt=self.extraction_prompt, few_shot_examples=list(self.few_shot_examples))

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError('Skill must be an object.')
        try:
            return cls(raw['id'], raw['name'], raw.get('description', ''), raw['detection_prompt'],
                       [CustomFieldDefinition.from_dict(f) for f in raw['fields']], raw.get('actions', []),
                       raw.get('enabled', True), raw.get('version', 1), raw.get('extraction_prompt', ''),
                       raw.get('few_shot_examples', ()))
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid skill definition.') from exc
