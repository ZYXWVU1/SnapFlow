"""Snapshot inputs; each action receives an independent copy."""
from copy import deepcopy
from dataclasses import dataclass
from src.skills.base import SkillResult


@dataclass(frozen=True)
class WorkflowContext:
    _result: SkillResult
    request_id: str | None = None
    screenshot_id: str | None = None

    def __post_init__(self):
        if not isinstance(self._result, SkillResult):
            raise ValueError('A SkillResult is required.')
        object.__setattr__(self, '_result', deepcopy(self._result))

    @classmethod
    def from_result(cls, result, request_id=None, screenshot_id=None):
        return cls(result, request_id, screenshot_id)

    @property
    def result(self):
        return deepcopy(self._result)

    @property
    def skill_id(self):
        return self._result.skill_id

    @property
    def data(self):
        return deepcopy(self._result.data)
