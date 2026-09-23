"""Adapt a definition to the existing Skill extraction/worker contract."""
from dataclasses import replace
from src.skills.base import Skill
from .prompt_builder import extraction_prompt
from .validator import validate_extraction


class RuntimeCustomSkill(Skill):
    def __init__(self, definition):
        self.definition = definition
        self.id, self.title = definition.id, definition.name
        self.action_ids = definition.actions
        self.presentation = tuple((f.id, f.label) for f in definition.fields)

    def prompt(self):
        return extraction_prompt(self.definition)

    def normalize(self, raw):
        return validate_extraction(self.definition, raw)

    def parse(self, response, confidence=0.0):
        result = super().parse(response, confidence)
        return replace(result, presentation=tuple((f.id, f.label, f.required) for f in self.definition.fields))
