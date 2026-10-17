"""Versioned, bounded definitions. Runtime IDs and capture evidence never persist here."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
import re
from uuid import uuid4

from src.agent.models import canonical, digest, snapshot, thaw
from src.desktop_actions.models import ACTIONS, PROPERTIES, RISKS, DesktopTarget, now


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('Use a short identifier starting with a letter.')


def bounded(value, maximum=500):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError('Text is empty or exceeds the limit.')


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


class Definition:
    def to_dict(self):
        return plain(self)

    @property
    def fingerprint(self):
        return digest(self.to_dict())


@dataclass(frozen=True)
class AutomationInput(Definition):
    input_id: str
    label: str
    value_type: str = 'string'
    required: bool = True
    default_value: object = None
    description: str = ''
    sensitive: bool = False
    enum_values: tuple = ()
    max_length: int = 4096

    def __post_init__(self):
        identifier(self.input_id)
        bounded(self.label, 200)
        if self.value_type not in ('string', 'text', 'number', 'boolean', 'enum', 'date', 'time'):
            raise ValueError('Unsupported input type.')
        if type(self.required) is not bool or type(self.sensitive) is not bool:
            raise ValueError('Invalid input flags.')
        if type(self.max_length) is not int or not 1 <= self.max_length <= 4096:
            raise ValueError('Invalid input length limit.')
        object.__setattr__(self, 'enum_values', tuple(self.enum_values))
        if len(self.enum_values) > 100 or self.value_type == 'enum' and not self.enum_values:
            raise ValueError('An enum needs bounded choices.')
        if any(type(v) is not str or not v or len(v) > 200 for v in self.enum_values):
            raise ValueError('Enum choices must be bounded text.')
        if self.sensitive and self.default_value is not None:
            raise ValueError('Sensitive inputs must be supplied every time.')
        object.__setattr__(self, 'default_value', snapshot(self.default_value))


@dataclass(frozen=True)
class TargetTemplate(Definition):
    process_name: str
    control_type: str
    automation_id: str | None = None
    name_template: str | None = None
    class_name: str | None = None
    ancestor_templates: tuple[str, ...] = ()
    expected_actions: tuple[str, ...] = ()
    expected_patterns: tuple[str, ...] = ()
    confidence_threshold: float = .75
    window_name: str | None = None
    window_match: str = 'optional'

    def __post_init__(self):
        bounded(self.process_name, 200)
        DesktopTarget(self.process_name, control_type=self.control_type, automation_id=self.automation_id,
            name=self.name_template, class_name=self.class_name, ancestor_path=self.ancestor_templates)
        for key in ('ancestor_templates', 'expected_actions', 'expected_patterns'):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        if not set(self.expected_actions) <= set(ACTIONS) or not .75 <= self.confidence_threshold <= 1:
            raise ValueError('Invalid target capabilities or confidence.')
        if self.window_match not in ('exact', 'contains', 'pattern', 'optional') or self.window_match != 'optional' and not self.window_name:
            raise ValueError('Invalid window selector.')
        if self.window_name is not None:
            bounded(self.window_name, 500)

    @classmethod
    def from_element(cls, element, action):
        return cls(element.process_name, element.control_type, element.automation_id or None,
            element.name or None, element.class_name or None, element.ancestor_path, (action,),
            element.capabilities.patterns, window_name=element.risk_context[0] if element.risk_context else None,
            window_match='exact' if element.risk_context else 'optional')

    def target(self, bind=lambda v: v):
        return DesktopTarget(self.process_name, control_type=self.control_type,
            automation_id=self.automation_id, name=bind(self.name_template), class_name=self.class_name,
            ancestor_path=tuple(bind(p) for p in self.ancestor_templates),
            required_actions=self.expected_actions, required_patterns=self.expected_patterns)


@dataclass(frozen=True)
class ConditionTemplate(Definition):
    target: TargetTemplate
    property: str
    equals: object

    def __post_init__(self):
        if not isinstance(self.target, TargetTemplate) or self.property not in PROPERTIES:
            raise ValueError('Invalid verification condition.')
        object.__setattr__(self, 'equals', snapshot(self.equals))


@dataclass(frozen=True)
class AutomationStepTemplate(Definition):
    step_id: str
    title: str
    action_type: str
    target_template: TargetTemplate
    value_template: object = None
    preconditions: tuple[ConditionTemplate, ...] = ()
    postconditions: tuple[ConditionTemplate, ...] = ()
    dependencies: tuple[str, ...] = ()
    risk_level: str = 'unknown'
    approval_mode: str = 'ask_each_action'
    optional: bool = False
    fallback_policy: str = 'structured_only'
    confidence: float = 1.0
    reviewed: bool = False
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.step_id)
        bounded(self.title, 200)
        if self.action_type not in ACTIONS or not isinstance(self.target_template, TargetTemplate):
            raise ValueError('Only registered semantic desktop actions are supported.')
        if self.risk_level not in RISKS or self.approval_mode != 'ask_each_action' or self.fallback_policy != 'structured_only':
            raise ValueError('A skill cannot weaken desktop approval or fallback policy.')
        if any(type(v) is not bool for v in (self.optional, self.reviewed)) or not 0 <= self.confidence <= 1:
            raise ValueError('Invalid step flags or confidence.')
        for key in ('preconditions', 'postconditions', 'dependencies', 'warnings'):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        if any(not isinstance(c, ConditionTemplate) for c in (*self.preconditions, *self.postconditions)) or max(len(self.preconditions), len(self.postconditions)) > 8:
            raise ValueError('Invalid bounded conditions.')
        object.__setattr__(self, 'value_template', snapshot(self.value_template))


@dataclass(frozen=True)
class AutomationSkill(Definition):
    name: str
    steps: tuple[AutomationStepTemplate, ...]
    description: str = ''
    skill_id: str = field(default_factory=lambda: 'skill_' + uuid4().hex)
    version_id: str = field(default_factory=lambda: 'version_' + uuid4().hex)
    inputs: tuple[AutomationInput, ...] = ()
    preconditions: tuple[ConditionTemplate, ...] = ()
    postconditions: tuple[ConditionTemplate, ...] = ()
    supported_apps: tuple[str, ...] = ()
    status: str = 'draft'
    trust: str = 'personal'
    tags: tuple[str, ...] = ()
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        identifier(self.skill_id)
        identifier(self.version_id)
        bounded(self.name, 200)
        if type(self.description) is not str or len(self.description) > 2000:
            raise ValueError('Description exceeds the limit.')
        for key in ('steps', 'inputs', 'preconditions', 'postconditions', 'supported_apps', 'tags'):
            object.__setattr__(self, key, tuple(getattr(self, key)))
        if not 1 <= len(self.steps) <= 20 or len(self.inputs) > 32 or len(self.tags) > 20:
            raise ValueError('Skill exceeds supported bounds.')
        if self.status not in ('draft', 'tested', 'published', 'archived', 'disabled') or self.trust not in ('personal', 'imported_unreviewed', 'imported_reviewed'):
            raise ValueError('Invalid skill lifecycle.')
        seen = set()
        for step in self.steps:
            if not isinstance(step, AutomationStepTemplate) or step.step_id in seen or not set(step.dependencies) <= seen:
                raise ValueError('Steps must be unique and ordered after their dependencies.')
            seen.add(step.step_id)
        if any(not isinstance(i, AutomationInput) for i in self.inputs) or len({i.input_id for i in self.inputs}) != len(self.inputs):
            raise ValueError('Inputs must have distinct identifiers.')
        for app in self.supported_apps:
            bounded(app, 200)
        for tag in self.tags:
            bounded(tag, 80)
        if max(len(self.preconditions), len(self.postconditions)) > 8 or any(not isinstance(c, ConditionTemplate) for c in (*self.preconditions, *self.postconditions)):
            raise ValueError('Invalid skill conditions.')
        canonical(self.to_dict(), limit=262144)

    @property
    def risk_summary(self):
        return {risk: sum(s.risk_level == risk for s in self.steps) for risk in RISKS if any(s.risk_level == risk for s in self.steps)}

    @classmethod
    def from_dict(cls, payload):
        data = dict(payload)
        def condition(raw):
            return ConditionTemplate(TargetTemplate(**raw['target']), raw['property'], raw['equals'])
        data['inputs'] = tuple(AutomationInput(**i) for i in data.get('inputs', ()))
        steps = []
        for raw in data['steps']:
            step = dict(raw)
            step['target_template'] = TargetTemplate(**step['target_template'])
            for key in ('preconditions', 'postconditions'):
                step[key] = tuple(condition(c) for c in step.get(key, ()))
            steps.append(AutomationStepTemplate(**step))
        data['steps'] = tuple(steps)
        for key in ('preconditions', 'postconditions'):
            data[key] = tuple(condition(c) for c in data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True)
class DemonstrationEvent:
    event_id: str
    timestamp: str
    event_type: str
    before: object = None
    after: object = None


@dataclass
class DemonstrationSession:
    session_id: str
    goal_description: str
    allowed_apps: tuple[str, ...]
    started_at: str = field(default_factory=now)
    ended_at: str | None = None
    status: str = 'recording'
    events: list = field(default_factory=list)
    capture_policy: str = 'semantic_only_no_clipboard_no_keys_no_screenshots'
    incomplete: bool = False


@dataclass(frozen=True)
class AutomationSkillTestCase(Definition):
    test_id: str
    skill_id: str
    inputs: object = field(default_factory=dict)
    expected_preconditions: tuple[ConditionTemplate, ...] = ()
    expected_postconditions: tuple[ConditionTemplate, ...] = ()
    synthetic_environment: str | None = None

    def __post_init__(self):
        identifier(self.test_id)
        identifier(self.skill_id)
        object.__setattr__(self, 'inputs', snapshot(self.inputs))
