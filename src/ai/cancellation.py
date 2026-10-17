"""Stop waiting for synchronous SDK work while keeping outstanding work bounded."""
from concurrent.futures import Future
import threading
import time

_outstanding = threading.BoundedSemaphore(4)


def run_cancellable(operation, cancelled=None, timeout=120):
    cancelled = cancelled or (lambda: False)
    if timeout <= 0:
        raise ValueError('AI request timed out.')
    if cancelled():
        raise ValueError('AI request cancelled.')
    if not _outstanding.acquire(blocking=False):
        raise ValueError('AI is busy finishing earlier requests. Please retry shortly.')
    future = Future()
    finished = threading.Event()

    def run():
        try:
            future.set_result(operation())
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            _outstanding.release()
            finished.set()

    threading.Thread(target=run, name='snapflow-ai-request', daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if cancelled():
            raise ValueError('AI request cancelled.')
        if future.done():
            return future.result()
        if time.monotonic() >= deadline:
            raise ValueError('AI request timed out.')
        finished.wait(min(.02, max(0, deadline - time.monotonic())))
