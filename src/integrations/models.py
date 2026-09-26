"""Non-secret integration metadata and connection state."""
from dataclasses import asdict, dataclass
import re


_ID = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
STATUSES = frozenset({'disconnected', 'connected', 'needs_reconnect', 'error'})


def valid_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid integration identifier.')
    return value


@dataclass(frozen=True)
class IntegrationDefinition:
    id: str
    name: str
    description: str
    auth_type: str
    capabilities: tuple[str, ...]

    def __post_init__(self):
        valid_id(self.id)
        if not self.name or not self.description or self.auth_type not in ('oauth', 'api_token'):
            raise ValueError('Invalid integration definition.')
        if not self.capabilities or any(not isinstance(item, str) or not item for item in self.capabilities):
            raise ValueError('Integration capabilities are required.')
        object.__setattr__(self, 'capabilities', tuple(self.capabilities))


@dataclass(frozen=True)
class IntegrationConnection:
    integration_id: str
    status: str = 'disconnected'
    account_label: str | None = None
    connected_at: str | None = None
    last_checked: str | None = None
    granted_capabilities: tuple[str, ...] = ()

    def __post_init__(self):
        valid_id(self.integration_id)
        if self.status not in STATUSES:
            raise ValueError('Invalid integration status.')
        for value in (self.account_label, self.connected_at, self.last_checked):
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValueError('Invalid connection metadata.')
        if not isinstance(self.granted_capabilities, (list, tuple)) or any(
            not isinstance(item, str) or not _ID.fullmatch(item) for item in self.granted_capabilities
        ):
            raise ValueError('Invalid granted capabilities.')
        object.__setattr__(self, 'granted_capabilities', tuple(self.granted_capabilities))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        expected = {'integration_id', 'status', 'account_label', 'connected_at',
                    'last_checked', 'granted_capabilities'}
        if not isinstance(value, dict) or set(value) - expected:
            raise ValueError('Invalid connection metadata.')
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError('Invalid connection metadata.') from exc
