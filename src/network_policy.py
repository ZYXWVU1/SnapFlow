"""Shared, live Private Mode boundary for application-owned network requests.

This policy permits only literal loopback HTTP endpoints and approved stdio MCP
while private. It never resolves a hostname to decide that a server is local.
"""
import ipaddress
import threading
from urllib.parse import urlsplit


class NetworkPolicyError(ValueError):
    """A request would cross the active privacy boundary."""


def is_loopback_url(destination):
    """Accept explicit HTTP(S) loopback endpoints without ambiguous URL syntax."""
    if (not isinstance(destination, str) or not destination or
            any(character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in destination) or '\\' in destination):
        return False
    try:
        parsed = urlsplit(destination)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in ('http', 'https') or not host or not host.isascii() or
                parsed.username is not None or parsed.password is not None or parsed.fragment or
                '%' in host or parsed.netloc.endswith(':') or port == 0):
            return False
        if host.lower() == 'localhost':
            return True
        address = ipaddress.ip_address(host)
        return (address.version == 4 and address.is_loopback) or address == ipaddress.IPv6Address('::1')
    except (ValueError, AttributeError):
        return False


class NetworkPolicyService:
    """Thread-safe privacy state consulted immediately before each dispatch."""
    _REMOTE_PURPOSES = frozenset({'cloud_ai', 'remote_embedding', 'external_ai_evaluation',
                                  'remote_evaluation'})

    def __init__(self, private_mode=False):
        self._lock = threading.RLock()
        self.set_private_mode(private_mode)

    @property
    def private_mode(self):
        with self._lock:
            return self._private_mode

    def set_private_mode(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Private Mode must be enabled or disabled.')
        with self._lock:
            self._private_mode = enabled

    def is_request_allowed(self, destination, purpose, contains_user_content=True):
        # Optional metadata traffic is blocked too: no hidden outbound exception.
        with self._lock:
            if not self._private_mode:
                return True
            if purpose in self._REMOTE_PURPOSES:
                return False
            if destination == 'stdio' and purpose == 'mcp_stdio':
                return True
            return is_loopback_url(destination)

    def require_allowed(self, destination, purpose, contains_user_content=True):
        if not self.is_request_allowed(destination, purpose, contains_user_content):
            raise NetworkPolicyError('Private Mode blocks this network operation. '
                                     'Disable Private Mode to use remote services.')


_NETWORK_POLICY = NetworkPolicyService()


def get_network_policy():
    return _NETWORK_POLICY


def require_http_request(request, purpose, contains_user_content=True):
    """Synchronous HTTP request hook shared by local and integration clients."""
    get_network_policy().require_allowed(str(request.url), purpose, contains_user_content)
