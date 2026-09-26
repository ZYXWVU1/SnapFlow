"""Atomic storage for non-secret connection metadata."""
import json
import os
from pathlib import Path
import tempfile
import threading

from src.skills.custom.storage import default_path
from .models import IntegrationConnection


class ConnectionStorage:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path().with_name('integrations.json')
        self.warning = ''
        self._read_only = False
        self._connections = {}
        self._lock = threading.RLock()
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or type(raw.get('version')) is not int or \
                    raw['version'] != 1 or not isinstance(raw.get('connections'), list):
                raise ValueError('Invalid integration metadata.')
            for item in raw['connections']:
                connection = IntegrationConnection.from_dict(item)
                if connection.integration_id in self._connections:
                    raise ValueError('Duplicate integration metadata.')
                self._connections[connection.integration_id] = connection
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.warning = 'Integration metadata could not be read. Changes are disabled.'
            self._read_only = True

    def get(self, integration_id):
        with self._lock:
            return self._connections.get(integration_id)

    def list_connections(self):
        with self._lock:
            return tuple(self._connections.values())

    def save(self, connection):
        with self._lock:
            if self._read_only:
                raise ValueError(self.warning)
            if not isinstance(connection, IntegrationConnection):
                raise ValueError('Invalid connection metadata.')
            verified = IntegrationConnection.from_dict(connection.to_dict())
            next_items = self._connections | {verified.integration_id: verified}
            self._commit(next_items)

    def delete(self, integration_id):
        with self._lock:
            if self._read_only:
                raise ValueError(self.warning)
            self._commit({key: value for key, value in self._connections.items() if key != integration_id})

    def _commit(self, connections):
        payload = {'version': 1, 'connections': [item.to_dict() for item in connections.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='integrations_', suffix='.tmp', dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            raw = json.loads(temporary.read_text(encoding='utf-8'))
            for item in raw['connections']:
                IntegrationConnection.from_dict(item)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._connections = connections
