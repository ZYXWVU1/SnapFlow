"""Immutable, bounded mission definitions. Runtime state is stored separately."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from uuid import uuid4

SESSION_STATUSES = ('draft', 'planning', 'awaiting_approval', 'running', 'paused', 'blocked',
                    'replanning', 'completed', 'failed', 'cancelled', 'interrupted')
STEP_STATUSES = ('pending', 'ready', 'awaiting_approval', 'running', 'success', 'failed',
                 'skipped', 'blocked', 'cancelled', 'interrupted', 'verification_failed')
STEP_TYPES = ('reason', 'retrieve_memory', 'read_context', 'run_skill', 'run_workflow',
              'call_action', 'call_mcp_tool', 'generate_text', 'compare_data', 'verify_result',
              'request_user_input', 'save_to_memory', 'desktop_action')
RISKS = ('read_only', 'local_write', 'external_write', 'sensitive', 'destructive', 'unknown')
APPROVAL_MODES = ('automatic_safe', 'approve_once', 'approve_at_execution', 'disabled')


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid Agent identifier.')
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


def canonical(value, limit=262144):
    text = json.dumps(thaw(value), ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > limit:
        raise ValueError('Agent data exceeds its size limit.')
    return text


def freeze(value, depth=0):
    if depth > 24:
        raise ValueError('Agent data is too deeply nested.')
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise ValueError('Agent fields must have string names.')
        return MappingProxyType({k: freeze(v, depth + 1) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v, depth + 1) for v in value)
    if value is not None and type(value) not in (str, bool, int, float):
        raise ValueError('Only JSON data is allowed.')
    if type(value) is float and not math.isfinite(value):
        raise ValueError('Only finite JSON numbers are allowed.')
    return value


def snapshot(value):
    result = freeze(value)
    canonical(result)
    return result


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _text(value, maximum, *, required=False):
    if type(value) is not str or len(value) > maximum or (required and not value.strip()):
        raise ValueError('Invalid or oversized Agent text.')


def _cost(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError('Invalid Agent cost estimate.')


class Contract:
    def to_dict(self):
        return {f.name: thaw(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, raw):
        if type(raw) is not dict:
            raise ValueError('Agent contract must be an object.')
        try:
            return cls(**raw)
        except TypeError as exc:
            raise ValueError('Unknown or missing Agent fields.') from exc


@dataclass(frozen=True)
class AgentConstraints(Contract):
    allow_external_writes: bool = False
    allow_remote_ai: bool = True
    allow_remote_mcp: bool = False
    allow_memory_search: bool = False
    allowed_workflows: tuple[str, ...] | None = None
    forbidden_capabilities: tuple[str, ...] = ()
    max_steps: int = 10
    max_external_writes: int = 1
    max_estimated_cost: float | None = None
    max_ai_calls: int = 5
    max_replans: int = 2
    max_runtime_minutes: int = 10
    allow_local_writes: bool = False
    max_ui_actions: int = 20
    max_external_commits: int = 2

    def __post_init__(self):
        for name in ('allow_external_writes', 'allow_remote_ai', 'allow_remote_mcp', 'allow_memory_search', 'allow_local_writes'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('Agent permissions must be booleans.')
        for name, low, high in (('max_steps', 1, 50), ('max_external_writes', 0, 10),
                                ('max_ai_calls', 0, 50), ('max_replans', 0, 5), ('max_runtime_minutes', 1, 60),
                                ('max_ui_actions', 0, 20), ('max_external_commits', 0, 2)):
            if type(getattr(self, name)) is not int or not low <= getattr(self, name) <= high:
                raise ValueError('Invalid Agent budget.')
        _cost(self.max_estimated_cost)
        for name in ('allowed_workflows', 'forbidden_capabilities'):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, (list, tuple)) or len(value) > 256 or any(type(x) is not str or len(x) > 256 for x in value):
                    raise ValueError('Invalid capability constraints.')
                object.__setattr__(self, name, tuple(value))

    def effective(self, *, private=False):
        return replace(self, allow_remote_ai=False, allow_remote_mcp=False, allow_external_writes=False) if private else self

    @property
    def budget(self):
        return AgentBudget(self.max_steps, self.max_ai_calls, self.max_external_writes,
            self.max_runtime_minutes, self.max_estimated_cost)


@dataclass(frozen=True)
class AgentBudget(Contract):
    max_steps: int = 10
    max_ai_calls: int = 5
    max_external_writes: int = 1
    max_runtime_minutes: int = 10
    max_estimated_cost: float | None = None

    def __post_init__(self):
        AgentConstraints(max_steps=self.max_steps, max_ai_calls=self.max_ai_calls,
            max_external_writes=self.max_external_writes, max_runtime_minutes=self.max_runtime_minutes,
            max_estimated_cost=self.max_estimated_cost)


@dataclass(frozen=True)
class AgentSession(Contract):
    session_id: str
    goal: str
    status: str = 'draft'
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    context_scope: str = 'current_only'
    current_plan_id: str | None = None
    execution_mode: str = 'guided'
    privacy_mode: str = 'standard'
    budget_policy_id: str | None = None
    active: bool = True
    constraints: AgentConstraints = field(default_factory=AgentConstraints)
    normalized_goal: str = ''
    user_constraints: tuple[str, ...] = ()
    elapsed_runtime_seconds: float = 0.0

    def __post_init__(self):
        identifier(self.session_id)
        _text(self.goal, 500, required=True)
        _text(self.normalized_goal, 500)
        _cost(self.elapsed_runtime_seconds)
        if self.elapsed_runtime_seconds is None:
            raise ValueError('Runtime accounting must be a finite nonnegative value.')
        if self.status not in SESSION_STATUSES or self.context_scope not in ('current_only', 'selected_memories', 'authorized_memory_search'):
            raise ValueError('Invalid mission state or context scope.')
        if self.execution_mode not in ('guided', 'plan_only') or self.privacy_mode not in ('standard', 'private', 'local_only') or type(self.active) is not bool:
            raise ValueError('Invalid Agent execution policy.')
        if isinstance(self.constraints, dict):
            object.__setattr__(self, 'constraints', AgentConstraints.from_dict(self.constraints))
        if not isinstance(self.constraints, AgentConstraints):
            raise ValueError('Typed goal constraints are required.')
        object.__setattr__(self, 'user_constraints', tuple(self.user_constraints))
        for value in self.user_constraints:
            _text(value, 500)

    def to_dict(self):
        raw = super().to_dict()
        raw['constraints'] = self.constraints.to_dict()
        return raw

    @classmethod
    def new(cls, goal, **kwargs):
        return cls(uuid4().hex, goal.strip(), normalized_goal=goal.strip(), **kwargs)


@dataclass(frozen=True)
class AgentStep(Contract):
    step_id: str
    order: int
    title: str
    description: str
    step_type: str
    capability_id: str | None = None
    input_bindings: Mapping = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    expected_output: Mapping | None = None
    verification: Mapping | None = None
    risk_level: str = 'unknown'
    approval_mode: str = 'disabled'
    optional: bool = False
    estimated_cost: float | None = None
    estimated_duration_ms: float | None = None

    def __post_init__(self):
        identifier(self.step_id)
        if type(self.order) is not int or not 1 <= self.order <= 50 or self.step_type not in STEP_TYPES:
            raise ValueError('Unsupported Agent step.')
        _text(self.title, 200, required=True)
        _text(self.description, 2000)
        if self.capability_id is not None:
            _text(self.capability_id, 256, required=True)
        if self.risk_level not in RISKS or self.approval_mode not in APPROVAL_MODES or type(self.optional) is not bool:
            raise ValueError('Invalid step risk or approval mode.')
        _cost(self.estimated_cost)
        _cost(self.estimated_duration_ms)
        if not isinstance(self.dependencies, (list, tuple)) or len(self.dependencies) > 50:
            raise ValueError('Invalid Agent dependencies.')
        object.__setattr__(self, 'dependencies', tuple(identifier(x) for x in self.dependencies))
        for name in ('input_bindings', 'expected_output', 'verification'):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ValueError('Agent step data must be objects.')
                object.__setattr__(self, name, snapshot(value))


@dataclass(frozen=True)
class AgentPlan(Contract):
    plan_id: str
    session_id: str
    version: int
    goal_summary: str
    steps: tuple[AgentStep, ...]
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    created_at: str = field(default_factory=now)
    supersedes_plan_id: str | None = None

    def __post_init__(self):
        identifier(self.plan_id)
        identifier(self.session_id)
        _text(self.goal_summary, 500, required=True)
        if type(self.version) is not int or not 1 <= self.version <= 100 or not isinstance(self.steps, (list, tuple)) or not 1 <= len(self.steps) <= 50:
            raise ValueError('Invalid Agent plan version or length.')
        object.__setattr__(self, 'steps', tuple(AgentStep.from_dict(s) if isinstance(s, dict) else s for s in self.steps))
        if any(not isinstance(s, AgentStep) for s in self.steps):
            raise ValueError('Invalid plan steps.')
        for name in ('assumptions', 'warnings'):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or len(values) > 20:
                raise ValueError('Invalid plan notes.')
            for value in values:
                _text(value, 1000)
            object.__setattr__(self, name, tuple(values))
        canonical(self.to_dict())

    def to_dict(self):
        raw = super().to_dict()
        raw['steps'] = [s.to_dict() for s in self.steps]
        return raw

    @property
    def fingerprint(self):
        return digest(self.to_dict())
