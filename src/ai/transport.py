"""Bounded reusable SDK clients with safe retirement of active requests."""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import threading


@dataclass(eq=False)
class _Lease:
    client: object
    active: int = 0
    retired: bool = False


class ClientPool:
    def __init__(self, capacity=4):
        self.capacity = capacity
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _close(client):
        try:
            client.close()
        except Exception:
            # Cleanup must not replace a request result or expose transport data.
            pass

    @contextmanager
    def lease(self, key, factory):
        with self._lock:
            if self._closed:
                raise ValueError('AI transport is closed.')
            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= self.capacity:
                    idle = next((k for k, value in self._entries.items() if not value.active), None)
                    if idle is not None:
                        self._close(self._entries.pop(idle).client)
                entry = _Lease(factory())
                if len(self._entries) < self.capacity:
                    self._entries[key] = entry
                else:
                    # Busy pools must not grow their retained client population.
                    entry.retired = True
            if key in self._entries:
                self._entries.move_to_end(key)
            entry.active += 1
        try:
            yield entry.client
        finally:
            with self._lock:
                entry.active -= 1
                close = entry.client if entry.retired and not entry.active else None
            if close is not None:
                self._close(close)

    def invalidate(self):
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.retired = True
            idle = [entry.client for entry in entries if not entry.active]
        for client in idle:
            self._close(client)

    def close(self):
        with self._lock:
            self._closed = True
        self.invalidate()
