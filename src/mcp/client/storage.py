"""Atomic bounded configuration. Imported metadata carries no trust."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from src.paths import AppPaths
from .models import MCPConnectionProfile


class MCPStorage:
    def __init__(self, path=None, *, paths=None):
        self.path = Path(path) if path is not None else (paths or AppPaths()).data_dir / 'mcp_connections.json'
        self._lock = RLock()
        self.warning = ''
        self._data = {'version': 1, 'profiles': [], 'policies': {}}
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Oversized MCP metadata.')
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            self._validate(raw)
            self._data = raw
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RecursionError):
            self.warning = 'MCP metadata could not be read. Changes are disabled.'

    @staticmethod
    def _validate(raw):
        if (not isinstance(raw, dict) or set(raw) != {'version', 'profiles', 'policies'} or
                type(raw['version']) is not int or raw['version'] != 1 or
                not isinstance(raw['profiles'], list) or len(raw['profiles']) > 64 or
                not isinstance(raw['policies'], dict) or len(raw['policies']) > 10000):
            raise ValueError('Invalid MCP metadata.')
        profiles = [MCPConnectionProfile.from_dict(item) for item in raw['profiles']]
        if len({p.id for p in profiles}) != len(profiles):
            raise ValueError('Duplicate MCP profile.')
        for key, policy in raw['policies'].items():
            if (not isinstance(key, str) or not isinstance(policy, dict) or
                    set(policy) != {'fingerprint', 'risk', 'mode'} or
                    policy['risk'] not in ('read_only', 'local_write', 'external_write', 'sensitive', 'unknown', 'destructive') or
                    policy['mode'] not in ('disabled', 'ask_every_time', 'allowed') or
                    not isinstance(policy['fingerprint'], str)):
                raise ValueError('Invalid MCP tool policy.')

    def list_profiles(self):
        with self._lock:
            return tuple(MCPConnectionProfile.from_dict(item) for item in self._data['profiles'])

    def get_profile(self, connection_id):
        return next((p for p in self.list_profiles() if p.id == connection_id), None)

    def _commit(self, data):
        if self.warning:
            raise ValueError(self.warning)
        self._validate(data)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
        if len(payload.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('MCP metadata limit reached.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='mcp_', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self._data = data

    def save_profile(self, profile):
        verified = MCPConnectionProfile.from_dict(profile.to_dict())
        with self._lock:
            data = deepcopy(self._data)
            old = self.get_profile(verified.id)
            data['profiles'] = [p for p in data['profiles'] if p['id'] != verified.id] + [verified.to_dict()]
            if old is not None and old != verified:
                data['policies'] = {k: v for k, v in data['policies'].items() if not k.startswith(f'mcp:{verified.id}:')}
            self._commit(data)

    def delete_profile(self, connection_id):
        with self._lock:
            data = deepcopy(self._data)
            data['profiles'] = [p for p in data['profiles'] if p['id'] != connection_id]
            data['policies'] = {k: v for k, v in data['policies'].items() if not k.startswith(f'mcp:{connection_id}:')}
            self._commit(data)

    def get_policy(self, action_id):
        with self._lock:
            return deepcopy(self._data['policies'].get(action_id))

    def save_policy(self, action_id, policy):
        with self._lock:
            data = deepcopy(self._data)
            data['policies'][action_id] = deepcopy(policy)
            self._commit(data)

    def export_profiles(self):
        return {'format': 'snapflow-mcp-profiles', 'version': 1,
                'profiles': [replace(p, enabled=False).to_dict() for p in self.list_profiles()]}

    def reconcile_policies(self, connection_id, fingerprints):
        with self._lock:
            data = deepcopy(self._data)
            prefix = f'mcp:{connection_id}:'
            for key in tuple(data['policies']):
                if key.startswith(prefix) and data['policies'][key]['fingerprint'] != fingerprints.get(key[len(prefix):]):
                    del data['policies'][key]
            if data['policies'] != self._data['policies']:
                self._commit(data)

    def import_profiles(self, value):
        if (not isinstance(value, dict) or set(value) != {'format', 'version', 'profiles'} or
                value['format'] != 'snapflow-mcp-profiles' or value['version'] != 1):
            raise ValueError('Invalid MCP profile import.')
        profiles = [replace(MCPConnectionProfile.from_dict(p), enabled=False).to_dict() for p in value['profiles']]
        with self._lock:
            self._commit({'version': 1, 'profiles': profiles, 'policies': {}})
