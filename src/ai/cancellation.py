"""Stop waiting for synchronous SDK work while keeping outstanding work bounded."""
from concurrent.futures import Future
import threading
import time

_outstanding = threading.BoundedSemaphore(4)


def run_cancellable(operation, cancelled=None, timeout=120):
    if cancelled is None:
        return operation()
    if cancelled():
        raise ValueError('AI request cancelled.')
    if not _outstanding.acquire(blocking=False):
        raise ValueError('AI is busy finishing earlier requests. Please retry shortly.')
    future = Future()

    def run():
        try:
            future.set_result(operation())
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            _outstanding.release()

    threading.Thread(target=run, name='snapflow-ai-request', daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        if cancelled():
            raise ValueError('AI request cancelled.')
        if future.done():
            return future.result()
        if time.monotonic() >= deadline:
            raise ValueError('AI request timed out.')
        time.sleep(.02)
