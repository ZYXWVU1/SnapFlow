"""Content-free UIA focus signals only during a user-authorized visible session."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread
from .process import NativeProcessOwner


class FocusEventObserver:
    def __init__(self):
        self.changed = Event()
        self.stop_event = Event()
        self.lock = Lock()
        self.process = self.owner = self.thread = None

    def start(self):
        if sys.platform != 'win32' or self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.changed.clear()
        self.thread = Thread(target=self._run, name='desktop-focus-events', daemon=True)
        self.thread.start()

    def _run(self):
        process = owner = None
        try:
            if self.stop_event.is_set():
                return
            command = [str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                '-NoProfile', '-NonInteractive', '-MTA', '-ExecutionPolicy', 'Bypass', '-File',
                str(Path(__file__).with_name('uia_reader.ps1')), '-ObserveOnly', '1']
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            owner = NativeProcessOwner(process)
            with self.lock:
                self.process, self.owner = process, owner
                if self.stop_event.is_set():
                    owner.close()
            while not self.stop_event.is_set():
                line = process.stdout.readline(32)
                if not line:
                    break
                if line.strip() == b'changed':
                    self.changed.set()  # One coalesced bit, no focus/window history.
        except Exception:
            pass
        finally:
            if owner:
                owner.close()
            if process:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
            with self.lock:
                self.process = self.owner = None

    def consume(self):
        result = self.changed.is_set()
        self.changed.clear()
        return result

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.owner:
                self.owner.close()
            elif self.process and self.process.poll() is None:
                self.process.kill()
        self.changed.clear()
