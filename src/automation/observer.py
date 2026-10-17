"""Owned UIA event subscription process, bounded queues, explicit teardown."""
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
from threading import Event, Thread

from src.desktop_actions.models import DesktopElement, DesktopElementCapabilities, DesktopState
from src.desktop_actions.native import WindowsDesktopActionProvider
from src.desktop_context.process import NativeProcessOwner


class WindowsDemonstrationObserver:
    def __init__(self, recorder):
        self.recorder = recorder
        self.processes, self.threads, self.owners = [], [], []
        self.stopped = Event()
        self.queue = Queue(maxsize=512)
        self.baselines = {}
        self.ready = 0
        self.error = False
        self.event_count = 0

    def start(self, fences):
        self.close()
        self.stopped = Event()
        self.ready, self.error = 0, False
        try:
            for fence in fences:
                script = Path(__file__).parents[1] / 'desktop_actions' / 'uia_actions.ps1'
                command = [str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                    '-NoProfile', '-NonInteractive', '-MTA', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                self.processes.append(process)
                self.owners.append(NativeProcessOwner(process))
                process.stdin.write((json.dumps({'operation': 'observe', 'hwnd': fence.allowed_window,
                    'pid': fence.allowed_process_id}) + '\n').encode())
                process.stdin.flush()
                thread = Thread(target=self._read, args=(process, fence, self.stopped), daemon=True)
                self.threads.append(thread)
                thread.start()
        except Exception:
            self.close()
            raise

    def _read(self, process, fence, stopped):
        try:
            while not stopped.is_set():
                line = process.stdout.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    raise ValueError('Oversized UIA event.')
                value = json.loads(line.decode('utf-8-sig'))
                if self.queue.full():
                    self.error = True  # Do not compile an incomplete trace silently.
                    break
                self.queue.put_nowait((fence, value))
        except Exception:
            self.error = True
        finally:
            if not stopped.is_set():
                self.error = True

    def drain(self):
        while True:
            try:
                fence, data = self.queue.get_nowait()
            except Empty:
                break
            self.recorder._guard()
            if data.get('ready'):
                self.ready += 1
                continue
            if data.get('error'):
                self.error = True
                continue
            if data.get('baseline'):
                raw = dict(data['baseline'])
                caps = raw.pop('capabilities')
                element = DesktopElement(**raw, process_name=data['process_name'], capabilities=DesktopElementCapabilities(**caps))
                if self.recorder.safe(element):
                    self.baselines.setdefault(fence.fence_id, []).append(self.recorder.clean(element))
                continue
            if not self.recorder.session or self.recorder.session.status != 'recording':
                continue
            if data.get('protected'):
                with self.recorder.lock:
                    self.recorder._append('protected_field_interaction_detected')
                continue
            # Native has already checked foreground/security both sides of capture.
            # Returning to Finish must not erase an event captured in the target window.
            def state(raw):
                raw = dict(raw)
                caps = raw.pop('capabilities')
                element = DesktopElement(**raw, process_name=data['process_name'],
                    capabilities=DesktopElementCapabilities(**caps))
                return DesktopState(element.process_id, element.window_id, element.process_name,
                    fence.allowed_window, (element,))
            self.recorder.observe(state(data['before']), state(data['after']),
                invoked_id=data['after']['element_id'] if data.get('invoked') else None,
                timestamp=data.get('timestamp'), unknown_before=data.get('unknown_before', False))
            current = state(data['after']).elements[0]
            if self.recorder.safe(current):
                baseline = self.baselines.get(fence.fence_id, [])
                self.baselines[fence.fence_id] = [e for e in baseline if e.element_id != current.element_id] + [self.recorder.clean(current)]
            self.event_count += 1
        if self.error:
            self.recorder.pause()
            if self.recorder.session:
                self.recorder.session.incomplete = True
            raise RuntimeError('Teaching paused: UIA events are unavailable or the event budget was exceeded. Cancel and teach again.')

    def checkpoint(self):
        """User-requested bounded state comparison for providers that omit events."""
        import time
        deadline = time.monotonic() + 15
        while not self.stopped.is_set() and time.monotonic() < deadline:
            self.recorder._guard()
            for fence in self.recorder.fences:
                provider = WindowsDesktopActionProvider()
                if not provider.foreground_matches(fence):
                    continue
                current = provider.capture(fence, cancelled=self.stopped.is_set)
                if not provider.foreground_matches(fence):
                    return
                prior = DesktopState(current.process_id, current.window_id, current.process_name,
                    current.window_id, tuple(self.baselines.get(fence.fence_id, ())))
                self.recorder.observe(prior, current)
                self.baselines[fence.fence_id] = [self.recorder.clean(e) for e in current.elements if self.recorder.safe(e)]
                return
            time.sleep(.05)
        raise InterruptedError('Select a scoped application within 15 seconds for the checkpoint.')

    def close(self):
        self.stopped.set()
        for process in self.processes:
            try:
                if process.poll() is None:
                    process.stdin.write(b'stop\n')
                    process.stdin.flush()
                    process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
            finally:
                if process.poll() is None:
                    process.wait(timeout=2)
        for owner in self.owners:
            owner.close()
        for thread in self.threads:
            thread.join(timeout=1)
        for process in self.processes:
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        self.processes.clear()
        self.threads.clear()
        self.owners.clear()
        self.baselines.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
