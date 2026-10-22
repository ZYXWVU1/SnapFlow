"""Immutable browser identities and future approved request contracts."""
from dataclasses import dataclass, field, fields
from urllib.parse import urlsplit
from uuid import uuid4
import time

from src.agent.models import digest, snapshot, thaw

ACTIONS = frozenset(('read', 'focus', 'scroll', 'select_option', 'set_text', 'set_checked',
                     'inspect_state', 'highlight', 'controlled_navigation', 'activate_safe_control'))


def text(value, maximum=256, *, empty=False):
    if type(value) is not str or len(value) > maximum or not empty and not value.strip():
        raise ValueError('Invalid bounded browser text.')


def origin(value):
    text(value, 2048)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or '\\' in value or any(c.isspace() or ord(c) < 32 for c in value)
                or parsed.port == 0 or value.endswith(':')):
            raise ValueError('Expected an HTTP(S) origin without URL content.')
    except ValueError as exc:
        raise ValueError('Invalid browser origin.') from exc


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise ValueError('Invalid browser/frame identifier.')


def document_identity(value):
    if type(value) is not dict or set(value) != {'tab_id', 'frame_id', 'document_id', 'origin'}:
        raise ValueError('Invalid browser document identity.')
    integer(value['tab_id'])
    if type(value['frame_id']) is not int or value['frame_id'] != 0:
        raise ValueError('Only the authorized top-level document is supported.')
    text(value['document_id'], 128)
    origin(value['origin'])
    if value['origin'] in ('https://chromewebstore.google.com', 'https://chrome.google.com', 'https://microsoftedge.microsoft.com'):
        raise ValueError('SnapFlow cannot access this browser page.')
    return dict(value)


@dataclass(frozen=True)
class BrowserSession:
    session_id: str
    browser_kind: str
    connection_id: str
    extension_id: str
    tab_id: int
    frame_id: int
    document_id: str
    origin: str
    page_title: str = ''
    authorized_scope: str = 'read'
    permission_state: str = 'pending'
    started_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)
    status: str = 'pending'
    access_mode: str = 'active_tab'

    def __post_init__(self):
        for key in ('session_id', 'connection_id', 'extension_id', 'document_id'):
            text(getattr(self, key))
        integer(self.tab_id)
        integer(self.frame_id)
        origin(self.origin)
        text(self.page_title, 500, empty=True)
        if (self.browser_kind not in ('chrome', 'edge') or self.authorized_scope not in ('read', 'interact')
                or self.access_mode not in ('active_tab', 'site')
                or self.permission_state not in ('pending', 'authorized', 'revoked')
                or self.status not in ('pending', 'active', 'stopped', 'expired', 'disconnected')
                or type(self.started_at) not in (int, float) or type(self.expires_at) not in (int, float)
                or not 0 < self.expires_at - self.started_at <= 3600):
            raise ValueError('Invalid browser session scope or lifetime.')


@dataclass(frozen=True)
class BrowserTarget:
    browser_session_id: str
    tab_id: int
    frame_id: int
    document_id: str
    origin: str
    role: str = ''
    accessible_name: str = ''
    label: str = ''
    placeholder: str = ''
    stable_attributes: object = field(default_factory=dict)
    ancestor_context: tuple[str, ...] = ()
    expected_tag: str = ''
    expected_state: object = field(default_factory=dict)
    locator_version: int = 1

    def __post_init__(self):
        for key in ('browser_session_id', 'document_id'):
            text(getattr(self, key))
        origin(self.origin)
        integer(self.tab_id)
        integer(self.frame_id)
        for key in ('role', 'accessible_name', 'label', 'placeholder', 'expected_tag'):
            text(getattr(self, key), 500, empty=True)
        if self.locator_version != 1 or type(self.locator_version) is not int:
            raise ValueError('Unsupported browser locator version.')
        ancestors = tuple(self.ancestor_context)
        if len(ancestors) > 8:
            raise ValueError('Browser ancestry exceeds limit.')
        for item in ancestors:
            text(item, 500)
        object.__setattr__(self, 'ancestor_context', ancestors)
        for key in ('stable_attributes', 'expected_state'):
            value = snapshot(getattr(self, key))
            if not isinstance(thaw(value), dict) or len(value) > 20:
                raise ValueError('Invalid browser target properties.')
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class BrowserActionRequest:
    action_id: str
    agent_session_id: str
    agent_step_id: str
    browser_session_id: str
    target: BrowserTarget
    action_type: str
    value: object = None
    expected_preconditions: tuple = ()
    expected_postconditions: tuple = ()
    permission_scope: str = 'interact'
    risk_level: str = 'unknown'
    approval_id: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 60)

    def __post_init__(self):
        for key in ('action_id', 'agent_session_id', 'agent_step_id', 'browser_session_id'):
            text(getattr(self, key))
        if (not isinstance(self.target, BrowserTarget) or self.target.browser_session_id != self.browser_session_id
                or self.action_type not in ACTIONS or self.permission_scope not in ('read', 'interact')
                or self.risk_level not in ('read_only', 'local_write', 'external_write', 'unknown')
                or type(self.created_at) not in (int, float) or type(self.expires_at) not in (int, float)
                or not 0 < self.expires_at - self.created_at <= 120):
            raise ValueError('Invalid browser action request.')
        if self.approval_id is not None:
            text(self.approval_id)
        for key in ('value', 'expected_preconditions', 'expected_postconditions'):
            object.__setattr__(self, key, snapshot(getattr(self, key)))

    @property
    def fingerprint(self):
        data = {f.name: thaw(getattr(self, f.name)) for f in fields(self) if f.name != 'target'}
        data['target'] = {f.name: thaw(getattr(self.target, f.name)) for f in fields(self.target)}
        return digest(data)
