"""Validated non-secret profiles and bounded capability contracts."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import ipaddress
import re
from urllib.parse import urlsplit


def now():
    return datetime.now(timezone.utc).isoformat()


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value):
        raise ValueError('Connection ID must use lowercase letters, numbers, underscore or hyphen.')
    return value


def valid_tool_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
        raise ValueError('Unsupported MCP capability name.')
    return value


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('Enter a valid MCP endpoint.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query):
            raise ValueError('Use HTTPS or loopback HTTP, without credentials, query or fragment.')
        loopback = parsed.hostname.lower() == 'localhost'
        try:
            loopback |= ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme == 'http' and not loopback:
            raise ValueError('Remote MCP endpoints require HTTPS.')
    except (ValueError, AttributeError) as exc:
        raise ValueError('Use HTTPS or loopback HTTP, without credentials, query or fragment.') from exc
    return value


@dataclass(frozen=True)
class MCPConnectionProfile:
    id: str
    name: str
    transport: str = 'stdio'
    enabled: bool = False
    url: str | None = None
    executable: str | None = None
    args: tuple[str, ...] = ()
    working_directory: str | None = None
    auth_mode: str = 'none'
    secret_environment: tuple[str, ...] = ()
    timeout_seconds: float = 30
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        valid_id(self.id)
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError('Enter a connection name of at most 100 characters.')
        if type(self.enabled) is not bool or self.transport not in ('stdio', 'streamable_http'):
            raise ValueError('Unsupported MCP transport or enabled flag.')
        if self.auth_mode not in ('none', 'bearer', 'oauth'):
            raise ValueError('Unsupported authentication mode.')
        if (type(self.timeout_seconds) not in (int, float) or
                not 1 <= self.timeout_seconds <= 120):
            raise ValueError('Timeout must be between 1 and 120 seconds.')
        if not isinstance(self.args, (list, tuple)) or len(self.args) > 64 or any(
                not isinstance(arg, str) or len(arg) > 4096 or '\x00' in arg for arg in self.args):
            raise ValueError('Arguments must be a bounded list of strings.')
        if not isinstance(self.secret_environment, (list, tuple)) or len(self.secret_environment) > 32 or any(
                not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key)
                for key in self.secret_environment):
            raise ValueError('Environment configuration contains credential names only.')
        object.__setattr__(self, 'args', tuple(self.args))
        object.__setattr__(self, 'secret_environment', tuple(self.secret_environment))
        if self.transport == 'stdio':
            if (not isinstance(self.executable, str) or not self.executable.strip() or
                    len(self.executable) > 2048 or '\x00' in self.executable):
                raise ValueError('Enter an executable path.')
            base = self.executable.replace('\\', '/').rsplit('/', 1)[-1].lower()
            if base.removesuffix('.exe') in ('cmd', 'powershell', 'pwsh', 'bash', 'sh', 'wscript', 'cscript') or base.endswith(('.bat', '.cmd', '.ps1')):
                raise ValueError('Shell launchers and shell scripts are not supported.')
            if self.url is not None or self.auth_mode != 'none':
                raise ValueError('Stdio profiles cannot use an HTTP endpoint or bearer authentication.')
        else:
            validate_url(self.url)
            if self.executable is not None or self.args or self.secret_environment:
                raise ValueError('HTTP profiles cannot contain subprocess configuration.')
        if self.working_directory is not None and (not isinstance(self.working_directory, str) or
                not Path(self.working_directory).is_absolute() or not Path(self.working_directory).is_dir()):
            raise ValueError('Choose an existing absolute working directory.')
        if any(not isinstance(value, str) or len(value) > 64 for value in (self.created_at, self.updated_at)):
            raise ValueError('Invalid connection timestamp.')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Invalid connection profile.')
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError('Profile contains unknown or missing fields.') from exc


@dataclass(frozen=True)
class MCPToolDescriptor:
    connection_id: str
    name: str
    title: str
    description: str
    input_schema: dict
    output_schema: dict | None
    fingerprint: str

    @property
    def action_id(self):
        return f'mcp:{self.connection_id}:{self.name}'


@dataclass(frozen=True)
class CapabilitySnapshot:
    tools: tuple = ()
    resources: tuple = ()
    resource_templates: tuple = ()
    prompts: tuple = ()


@dataclass(frozen=True)
class ConnectionState:
    connection_id: str
    status: str = 'disconnected'
    server_name: str = ''
    server_version: str = ''
    protocol_version: str = ''
    capabilities: tuple[str, ...] = ()
    connected_at: str | None = None
    error: str = ''


@dataclass(frozen=True)
class InvocationResult:
    success: bool
    text: str
    structured: dict | None = None
    category: str = ''
    duration_seconds: float = 0
    truncated: bool = False
