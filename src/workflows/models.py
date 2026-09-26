"""Versioned JSON configuration and execution records."""
from dataclasses import dataclass, field, asdict
from copy import deepcopy
import json
import re

OPERATORS = ('exists', 'not_exists', 'equals', 'not_equals', 'contains', 'not_contains',
             'greater_than', 'less_than', 'is_empty', 'is_not_empty')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise ValueError('Invalid identifier.')


def boolean(value):
    if type(value) is not bool:
        raise ValueError('Expected a boolean.')


def json_copy(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Expected finite JSON data.') from exc


@dataclass(frozen=True)
class WorkflowTrigger:
    type: str
    skill_id: str

    def __post_init__(self):
        if self.type != 'skill_match':
            raise ValueError('Only skill_match triggers are supported.')
        identifier(self.skill_id)


@dataclass(frozen=True)
class WorkflowCondition:
    field: str
    operator: str
    value: object = None

    def __post_init__(self):
        identifier(self.field)
        if self.operator not in OPERATORS:
            raise ValueError('Unknown condition operator.')
        object.__setattr__(self, 'value', json_copy(self.value))


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    action_id: str
    config: dict = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self):
        identifier(self.id)
        identifier(self.action_id)
        boolean(self.enabled)
        if not isinstance(self.config, dict):
            raise ValueError('Action configuration must be an object.')
        object.__setattr__(self, 'config', json_copy(self.config))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    name: str
    trigger: WorkflowTrigger
    steps: tuple[WorkflowStep, ...]
    description: str = ''
    conditions: tuple[WorkflowCondition, ...] = ()
    enabled: bool = True
    failure_policy: str = 'stop'
    auto_run: bool = False
    version: int = 1

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError('Workflow name is required (maximum 200 characters).')
        if not isinstance(self.description, str) or len(self.description) > 4000:
            raise ValueError('Invalid workflow description.')
        if not isinstance(self.trigger, WorkflowTrigger):
            raise ValueError('Workflow trigger is required.')
        boolean(self.enabled)
        boolean(self.auto_run)
        if type(self.version) is not int or self.version != 1:
            raise ValueError('Unsupported workflow version.')
        if self.failure_policy not in ('stop', 'continue'):
            raise ValueError('Invalid failure policy.')
        for attr, cls, maximum in [('steps', WorkflowStep, 100), ('conditions', WorkflowCondition, 100)]:
            values = getattr(self, attr)
            if not isinstance(values, (list, tuple)) or len(values) > maximum or any(not isinstance(v, cls) for v in values):
                raise ValueError(f'Invalid {attr}.')
            object.__setattr__(self, attr, tuple(deepcopy(values)))
        if not self.steps or not any(s.enabled for s in self.steps):
            raise ValueError('At least one enabled action is required.')
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError('Step IDs must be unique.')

    def to_dict(self):
        return json_copy(asdict(self))

    @classmethod
    def from_dict(cls, raw):
        try:
            raw = json_copy(raw)
            if not isinstance(raw, dict):
                raise ValueError('Workflow must be an object.')
            if not isinstance(raw.get('steps'), list) or not isinstance(raw.get('conditions', []), list):
                raise ValueError('Steps and conditions must be arrays.')
            raw['trigger'] = WorkflowTrigger(**raw['trigger'])
            raw['steps'] = tuple(WorkflowStep(**s) for s in raw['steps'])
            raw['conditions'] = tuple(WorkflowCondition(**c) for c in raw.get('conditions', []))
            return cls(**raw)
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid workflow structure.') from exc


@dataclass
class WorkflowStepResult:
    step_id: str
    action_id: str
    status: str = 'pending'
    message: str = ''
    error: str | None = None
    output_path: str | None = None
    preview: str | None = None


@dataclass
class WorkflowExecutionResult:
    workflow_id: str
    status: str
    started_at: str
    steps: list[WorkflowStepResult]
    execution_id: str
    request_id: str | None = None
    finished_at: str | None = None
    error: str | None = None
    preview: bool = False
