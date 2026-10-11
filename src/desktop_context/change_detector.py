"""Ephemeral foreground debounce, used only during a visible session."""
import time


class ContextChangeDetector:
    def __init__(self, *, clock=time.monotonic, debounce_seconds=2):
        self.clock, self.debounce_seconds = clock, debounce_seconds
        self.pending = self.emitted = None
        self.since = 0

    def reset(self):
        self.pending = self.emitted = None

    def changed(self, window):
        key = (window.hwnd, window.process_id) if window else None
        if key != self.pending:
            self.pending, self.since = key, self.clock()
            return False
        if key and key != self.emitted and self.clock() - self.since >= self.debounce_seconds:
            self.emitted = key
            return True
        return False
