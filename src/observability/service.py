"""Application lifetime adapter; failure never prevents ordinary startup/quit."""
import logging
from threading import RLock

from .bus import AppEventBus
from .models import AppEvent
from .store import LocalEventStore


class ObservabilityService:
    def __init__(self, path, *, enabled=True):
        self.enabled = enabled
        self.store = None
        self.session = None
        self.warning = ''
        self._closed = False
        self._ready = False
        self._lock = RLock()
        if enabled:
            try:
                self.store = LocalEventStore(path)
            except Exception:
                self.warning = 'Local reliability records are unavailable. Existing files were preserved.'
                logging.getLogger(__name__).warning(self.warning)
        self.available = self.store is not None
        self.bus = AppEventBus((self.store.append,) if self.store else (), enabled=enabled and self.available)

    def start(self, *, private_mode):
        with self._lock:
            if self._closed or not self.available:
                return None
            if self.session is not None:
                return self.session
            try:
                self.session = self.store.begin_session(private_mode=private_mode)
            except Exception:
                self.available = False
                self.bus.enabled = False
                self.warning = 'Local reliability session could not be recorded.'
                logging.getLogger(__name__).warning(self.warning)
                return None
            if self.session.previous_session_unclean:
                self.emit('previous_session_unclean')
            self.emit('app_started', properties={'private_mode': private_mode})
            return self.session

    def emit(self, event_type, **values):
        with self._lock:
            if self._closed or self.session is None:
                return False
            try:
                return self.bus.publish(AppEvent(event_type, session_id=self.session.session_id,
                                                component=values.pop('component', 'application'), **values))
            except Exception:
                return False

    def ready(self):
        with self._lock:
            if not self._ready and not self._closed:
                self.emit('app_ready', success=True)
                self._ready = True

    def close(self, *, clean_shutdown=True):
        with self._lock:
            if self._closed:
                return
            if self.session is not None:
                self.emit('app_stopped' if clean_shutdown else 'recording_disabled', success=True)
                try:
                    self.store.finish_session(self.session.session_id, clean_shutdown=clean_shutdown)
                except Exception:
                    # Cannot assert a clean marker was persisted when the disk failed.
                    self.warning = 'Clean shutdown could not be recorded.'
                    logging.getLogger(__name__).warning(self.warning)
            self._closed = True
            self.bus.enabled = False
