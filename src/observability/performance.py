"""Content-free timings with bounded, non-blocking producer delivery."""
from contextlib import contextmanager, nullcontext
from functools import wraps
import math
import os
import queue
import threading
import time
from types import SimpleNamespace

from .models import AppEvent
from .privacy import sanitize_event

_active = None


class OperationProfiler:
    def __init__(self, service_provider, *, capacity=512):
        self.provider = service_provider
        self.queue = queue.Queue(maxsize=capacity)
        self.dropped = 0
        self._closed = False
        self._discard = False
        self._stop = threading.Event()
        self.worker = threading.Thread(target=self._consume, name='safe-operation-events', daemon=True)
        self.worker.start()

    def emit(self, event_type, **values):
        if self._closed:
            return False
        try:
            event = sanitize_event(AppEvent(event_type, **values))
            # Reject unknown operation labels instead of recording an empty trace.
            if event_type == 'operation_completed' and 'operation' not in event.properties:
                return False
            self.queue.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
        except Exception:
            pass
        return False

    def record(self, operation, component, duration_ms, success):
        return self.emit('operation_completed', component=component,
                         duration_ms=duration_ms, success=success, properties={'operation': operation})

    @contextmanager
    def trace(self, operation, component='application'):
        started = time.perf_counter()
        success = False
        outcome = SimpleNamespace(success=None)
        try:
            yield outcome
            success = outcome.success if type(outcome.success) is bool else True
        finally:
            self.record(operation, component, (time.perf_counter() - started) * 1000, success)

    def _consume(self):
        while not self._stop.is_set() or not self.queue.empty():
            try:
                event = self.queue.get(timeout=.05)
            except queue.Empty:
                continue
            try:
                if self._discard:
                    continue
                self.provider().emit(event.event_type, component=event.component,
                    duration_ms=event.duration_ms, success=event.success, properties=dict(event.properties))
            except Exception:
                self.dropped += 1
            finally:
                self.queue.task_done()

    def close(self, timeout=2, *, drain=True):
        self._closed = True
        self._discard = self._discard or not drain
        self._stop.set()
        self.worker.join(timeout)
        return not self.worker.is_alive()


def install_profiler(profiler):
    global _active
    _active = profiler


def trace(operation, component='application'):
    return _active.trace(operation, component) if _active else nullcontext()


def emit(event_type, **values):
    return _active.emit(event_type, **values) if _active else False


def measured(operation, component='application', *, event_type=None):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            success = False
            try:
                with trace(operation, component) as outcome:
                    result = function(*args, **kwargs)
                    success = result is not False and getattr(result, 'status', 'success') in ('success', 'completed')
                    if outcome is not None:
                        outcome.success = success
                    return result
            finally:
                if event_type:
                    emit(event_type, component=component, success=success,
                         properties={'search_type': 'fts'} if event_type == 'memory_search_completed' else {})
        return invoke
    return decorate


class ResourceSampler:
    """Process CPU per one core; working set on Windows. VRAM stays unknown."""
    def __init__(self):
        self.wall, self.cpu = time.monotonic(), time.process_time()

    def sample(self, *, workers=0, queue_depth=0):
        wall, cpu = time.monotonic(), time.process_time()
        values = {'cpu_percent': max(0., min(100000., 100 * (cpu - self.cpu) / max(.001, wall - self.wall))),
                  'threads': threading.active_count(), 'workers': workers, 'queue_depth': queue_depth}
        self.wall, self.cpu = wall, cpu
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('page_faults', wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged',
                        'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
            ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            ctypes.windll.kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            process = ctypes.windll.kernel32.GetCurrentProcess()
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                values['app_ram_mb'] = counters.ws / (1024 * 1024)
            handles = wintypes.DWORD()
            if ctypes.windll.kernel32.GetProcessHandleCount(process, ctypes.byref(handles)):
                values['handles'] = handles.value
        else:
            try:
                import resource
                values['app_ram_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            except ImportError:
                pass
        return {key: value for key, value in values.items()
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000000}
