"""Immutable, bounded values at the desktop action trust boundary."""
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
import math
from uuid import uuid4

from src.agent.models import Contract as AgentContract, digest, snapshot, thaw


class Contract(AgentContract):
    def to_dict(self):
        def plain(value):
            if isinstance(value, Contract):
                return value.to_dict()
            if isinstance(value, (tuple, list)):
                return [plain(v) for v in value]
            return thaw(value)
        return {f.name: plain(getattr(self, f.name)) for f in fields(self)}

ACTIONS = ('focus', 'invoke', 'toggle', 'select', 'expand', 'collapse', 'set_value',
           'set_range_value', 'scroll', 'scroll_into_view')
RISKS = ('ui_navigation', 'ui_local_edit', 'ui_external_edit', 'ui_external_commit',
         'ui_sensitive', 'ui_destructive', 'unknown')
PROPERTIES = ('value', 'toggle_state', 'selected', 'expanded', 'focused', 'enabled',
              'name', 'range_value', 'scroll_horizontal', 'scroll_vertical', 'offscreen')


def now():
    return datetime.now(timezone.utc).isoformat()


def expiry(seconds=120):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def expired(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError('Desktop timestamps require a timezone.')
    return datetime.now(timezone.utc) >= moment


def text(value, maximum=500, optional=False):
    if optional and value is None:
        return
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError('Invalid desktop action text.')


@dataclass(frozen=True)
class DesktopTarget(Contract):
    process_name: str | None = None
    window_title_hint: str | None = None
    control_type: str | None = None
    automation_id: str | None = None
    name: str | None = None
    class_name: str | None = None
    ancestor_path: tuple[str, ...] = ()
    runtime_id_hint: str | None = None
    bounds_hint: tuple | None = None
    source_snapshot_id: str | None = None
    source_element_id: str | None = None

    def __post_init__(self):
        for key in ('process_name', 'window_title_hint', 'control_type', 'automation_id', 'name',
                    'class_name', 'runtime_id_hint', 'source_snapshot_id', 'source_element_id'):
            text(getattr(self, key), optional=True)
        object.__setattr__(self, 'ancestor_path', tuple(self.ancestor_path))
        if len(self.ancestor_path) > 8:
            raise ValueError('Target ancestry exceeds the limit.')
        for part in self.ancestor_path:
            text(part, 1000)
        if self.bounds_hint is not None:
            bounds = tuple(self.bounds_hint)
            if len(bounds) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds):
                raise ValueError('Invalid target bounds.')
            object.__setattr__(self, 'bounds_hint', bounds)
        if not self.control_type or not (self.automation_id or self.name):
            raise ValueError('A semantic target needs a control type and name or automation ID.')


@dataclass(frozen=True)
class DesktopCondition(Contract):
    target: DesktopTarget
    property: str
    equals: object

    def __post_init__(self):
        if not isinstance(self.target, DesktopTarget) or self.property not in PROPERTIES:
            raise ValueError('Invalid desktop condition.')
        object.__setattr__(self, 'equals', snapshot(self.equals))


@dataclass(frozen=True)
class DesktopActionRequest(Contract):
    action_id: str
    target: DesktopTarget
    action_type: str
    value: object = None
    agent_session_id: str | None = None
    agent_step_id: str | None = None
    expected_preconditions: tuple[DesktopCondition, ...] = ()
    expected_postconditions: tuple[DesktopCondition, ...] = ()
    created_at: str = field(default_factory=now)
    expires_at: str = field(default_factory=expiry)

    def __post_init__(self):
        text(self.action_id, 128)
        text(self.agent_session_id, 128, optional=True)
        text(self.agent_step_id, 128, optional=True)
        if not isinstance(self.target, DesktopTarget) or self.action_type not in ACTIONS:
            raise ValueError('Unregistered desktop action or target.')
        object.__setattr__(self, 'value', snapshot(self.value))
        if self.action_type == 'set_value' and (type(self.value) is not str or len(self.value) > 4096):
            raise ValueError('Text entry requires at most 4096 characters.')
        if self.action_type == 'toggle' and type(self.value) is not bool:
            raise ValueError('Toggle requires an explicit desired boolean state.')
        if self.action_type == 'set_range_value' and (type(self.value) not in (int, float) or not math.isfinite(self.value)):
            raise ValueError('Range value must be finite.')
        if self.action_type == 'scroll':
            from collections.abc import Mapping
            if not isinstance(self.value, Mapping) or set(self.value) != {'horizontal', 'vertical'} or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in self.value.values()):
                raise ValueError('Scroll requires bounded horizontal and vertical percentages.')
        if self.action_type not in ('set_value', 'toggle', 'set_range_value', 'scroll') and self.value is not None:
            raise ValueError('This action accepts no value.')
        for key in ('expected_preconditions', 'expected_postconditions'):
            conditions = tuple(getattr(self, key))
            if len(conditions) > 8 or any(not isinstance(c, DesktopCondition) for c in conditions):
                raise ValueError('Invalid desktop conditions.')
            object.__setattr__(self, key, conditions)
        expired(self.created_at)
        expired(self.expires_at)

    @property
    def fingerprint(self):
        return digest(self.to_dict())


@dataclass(frozen=True)
class DesktopActionFence(Contract):
    allowed_process_id: int
    allowed_process_name: str
    allowed_window: int
    session_id: str
    allowed_action_types: tuple[str, ...] = ACTIONS
    expires_at: str = field(default_factory=lambda: expiry(600))
    fence_id: str = field(default_factory=lambda: uuid4().hex)
    max_ui_actions: int = 20
    max_external_commits: int = 2

    def __post_init__(self):
        if type(self.allowed_process_id) is not int or self.allowed_process_id <= 0 or type(self.allowed_window) is not int or self.allowed_window <= 0:
            raise ValueError('A fence must identify a live process/window.')
        text(self.allowed_process_name)
        text(self.session_id, 128)
        text(self.fence_id, 128)
        actions = tuple(self.allowed_action_types)
        if not actions or not set(actions) <= set(ACTIONS):
            raise ValueError('Invalid action fence.')
        object.__setattr__(self, 'allowed_action_types', actions)
        if type(self.max_ui_actions) is not int or not 1 <= self.max_ui_actions <= 20 or type(self.max_external_commits) is not int or not 0 <= self.max_external_commits <= 2:
            raise ValueError('Invalid desktop budget.')
        expired(self.expires_at)


@dataclass(frozen=True)
class DesktopElementCapabilities(Contract):
    actions: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesktopElement(Contract):
    element_id: str
    process_id: int
    window_id: int
    process_name: str
    control_type: str
    name: str = ''
    automation_id: str = ''
    class_name: str = ''
    ancestor_path: tuple[str, ...] = ()
    bounds: tuple | None = None
    protected: bool = False
    state: object = field(default_factory=dict)
    capabilities: DesktopElementCapabilities = field(default_factory=DesktopElementCapabilities)
    risk_context: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'ancestor_path', tuple(self.ancestor_path))
        object.__setattr__(self, 'risk_context', tuple(self.risk_context))
        object.__setattr__(self, 'state', snapshot(self.state))
        if self.bounds is not None:
            object.__setattr__(self, 'bounds', tuple(self.bounds))

    @property
    def fingerprint(self):
        # Bounds and runtime IDs are hints, never authority. Text is hashed in RAM only.
        return digest([self.process_id, self.window_id, self.control_type, self.automation_id,
                       self.name, self.class_name, self.ancestor_path, self.risk_context])

    def target(self, source_snapshot_id=None):
        return DesktopTarget(self.process_name, control_type=self.control_type,
            automation_id=self.automation_id or None, name=self.name or None,
            class_name=self.class_name or None, ancestor_path=self.ancestor_path,
            runtime_id_hint=self.element_id, bounds_hint=self.bounds,
            source_snapshot_id=source_snapshot_id, source_element_id=self.element_id)


@dataclass(frozen=True)
class DesktopState:
    process_id: int
    window_id: int
    process_name: str
    foreground_window: int
    elements: tuple[DesktopElement, ...]
    security_available: bool = True
    safe_error: str | None = None


@dataclass(frozen=True)
class TargetResolution:
    status: str
    confidence: float
    candidate_count: int
    resolved_element: DesktopElement | None = None
    reasons: tuple[str, ...] = ()
    candidates: tuple[DesktopElement, ...] = ()


@dataclass(frozen=True)
class DesktopActionPreview:
    preview_id: str
    request: DesktopActionRequest
    resolution: TargetResolution
    risk_level: str
    allowed: bool
    safe_error: str | None = None
    fence_id: str | None = None
    state_hash: str | None = None
    expires_at: str = field(default_factory=lambda: expiry(60))


@dataclass(frozen=True)
class DesktopVerification(Contract):
    verified: bool
    method: str
    reason: str


@dataclass(frozen=True)
class DesktopActionResult(Contract):
    action_id: str
    status: str
    target_resolved: bool = False
    execution_method: str = 'none'
    started_at: str = field(default_factory=now)
    finished_at: str = field(default_factory=now)
    before_state: object = None
    after_state: object = None
    verification_result: DesktopVerification | None = None
    warnings: tuple[str, ...] = ()
    safe_error: str | None = None

    def receipt(self):
        # Only a verified outcome gets success; native dispatch is never sufficient.
        return {'success': self.status == 'success', 'status': self.status,
            'desktop_verified': bool(self.verification_result and self.verification_result.verified),
            'action_id': self.action_id, 'execution_method': self.execution_method}
