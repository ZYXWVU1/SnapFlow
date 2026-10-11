"""Failure-isolated dispatch; never log caller values or subscriber exceptions."""
from threading import RLock

from .privacy import sanitize_event


class AppEventBus:
    def __init__(self, sinks=(), *, enabled=True):
        self.enabled = enabled
        self._subscribers = list(sinks)
        self._lock = RLock()
        self.subscriber_failures = 0
        self.rejected_events = 0

    def subscribe(self, subscriber):
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber):
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def publish(self, event):
        if not self.enabled:
            return False
        try:
            event = sanitize_event(event)
        except Exception:
            with self._lock:
                self.rejected_events += 1
            return False
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                with self._lock:
                    self.subscriber_failures += 1
                # Diagnostics must not become a new content leak via exc_info.
                # Counters expose delivery failures without recursive event publication.
        return True
