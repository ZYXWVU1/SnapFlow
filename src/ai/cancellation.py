"""Bounded synchronous work with cancellable background admission and real ownership."""
from concurrent.futures import Future
import threading
import time

_outstanding = threading.BoundedSemaphore(4)


class _Admission:
    """Leave capacity for interactive work; retain leases until SDK work exits."""
    def __init__(self):
        self.condition = threading.Condition()
        self.foreground = 0
        self.background = 0

    @staticmethod
    def check(cancelled, deadline):
        if cancelled():
            raise ValueError('AI request cancelled.')
        if time.monotonic() >= deadline:
            raise ValueError('AI request timed out.')

    def acquire(self, background, cancelled, deadline):
        with self.condition:
            while background and (self.foreground or self.background):
                self.check(cancelled, deadline)
                self.condition.wait(min(.02, max(0, deadline - time.monotonic())))
            self.check(cancelled, deadline)
            if background:
                self.background += 1
            else:
                self.foreground += 1

    def release(self, background):
        with self.condition:
            if background:
                self.background -= 1
            else:
                self.foreground -= 1
            self.condition.notify_all()


_admission = _Admission()


def run_cancellable(operation, cancelled=None, timeout=120, *, background=False):
    cancelled = cancelled or (lambda: False)
    if timeout <= 0:
        raise ValueError('AI request timed out.')
    deadline = time.monotonic() + timeout
    # Wait outside the global outstanding budget. Queued indexing/evaluation
    # cannot occupy all four slots while the user starts an interactive request.
    _admission.acquire(background, cancelled, deadline)
    if not _outstanding.acquire(blocking=False):
        _admission.release(background)
        raise ValueError('AI is busy finishing earlier requests. Please retry shortly.')
    future = Future()
    finished = threading.Event()

    def run():
        try:
            _admission.check(cancelled, deadline)
            future.set_result(operation())
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            _outstanding.release()
            _admission.release(background)
            finished.set()

    try:
        threading.Thread(target=run, name='snapflow-ai-request', daemon=True).start()
    except BaseException:
        _outstanding.release()
        _admission.release(background)
        raise
    while True:
        if cancelled():
            raise ValueError('AI request cancelled.')
        if future.done():
            return future.result()
        if time.monotonic() >= deadline:
            raise ValueError('AI request timed out.')
        finished.wait(min(.02, max(0, deadline - time.monotonic())))
