"""Explicit server sharing flags. No tokens or automatic imported grants."""
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import tempfile
import threading


@dataclass(frozen=True)
class ServerPolicy:
    enabled: bool = False
    share_memory: bool = False
    share_images: bool = False
    share_workflows: bool = False
    share_history: bool = False
    share_custom_skills: bool = False
    allow_workflow_execution: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError('Server permissions must be boolean.')


class ServerPolicyStore:
    def __init__(self, path):
        self.path = Path(path)
        self.warning = ''
        self._policy = ServerPolicy()
        self._lock = threading.RLock()
        self.revision = 0
        try:
            if self.path.stat().st_size > 4096:
                raise ValueError('Server settings too large.')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if set(data) != {'version', 'policy'} or type(data['version']) is not int or data['version'] != 1:
                raise ValueError('Invalid server settings.')
            self._policy = ServerPolicy(**data['policy'])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.warning = 'Server settings are invalid. Sharing remains disabled; the file will not be overwritten.'

    def snapshot(self):
        with self._lock:
            return self._policy

    def update(self, **flags):
        with self._lock:
            if self.warning:
                raise ValueError(self.warning)
            try:
                policy = replace(self._policy, **flags)
            except TypeError:
                raise ValueError('Unknown server permission.') from None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='mcp_server_', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'version': 1, 'policy': asdict(policy)}, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            self._policy = policy
            self.revision += 1
            return policy
