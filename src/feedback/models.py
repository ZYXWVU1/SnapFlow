"""A correction keeps the AI's original extraction untouched."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from src.skills.custom.validator import normalize_value


@dataclass
class EditableExtraction:
    result_id: str
    skill_id: str
    skill_version_id: str | None
    original_data: dict
    edited_data: dict
    changed_fields: list[str] = field(default_factory=list)
    updated_at: str = ''

    @classmethod
    def create(cls, skill_id, data, skill_version_id=None):
        original = deepcopy(data)
        return cls(uuid4().hex, skill_id, skill_version_id, original, deepcopy(original))

    def apply(self, values, definition):
        if not isinstance(values, dict):
            raise ValueError('Corrections must be field values.')
        result = deepcopy(self.edited_data)
        for field_def in definition.fields:
            if field_def.id not in values:
                continue
            raw = values[field_def.id]
            if raw == '' and field_def.field_type != 'list_string':
                raw = None
            normalized = normalize_value(field_def.field_type, raw)
            if raw is not None and normalized is None:
                raise ValueError(f'{field_def.label} must be a valid {field_def.field_type.replace("_", " ")} value.')
            if field_def.required and (normalized is None or normalized == []):
                raise ValueError(f'{field_def.label} is required.')
            result[field_def.id] = deepcopy(normalized)
        self.edited_data = result
        self.changed_fields = [f.id for f in definition.fields
                               if self.original_data.get(f.id) != result.get(f.id)]
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def reset(self):
        self.edited_data = deepcopy(self.original_data)
        self.changed_fields = []
        self.updated_at = datetime.now(timezone.utc).isoformat()
