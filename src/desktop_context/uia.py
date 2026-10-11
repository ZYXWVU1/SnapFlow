"""Killable UIA subprocess: a timeout never leaves an unbounded COM worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Lock

from .models import DesktopUIElement, UIAResult


class UIAutomationContextProvider:
    def __init__(self, *, timeout_seconds=4.0):
        self.timeout_seconds = min(10.0, max(.1, timeout_seconds))
        self._lock = Lock()

    def capture(self, window, *, include_ui=True, include_selection=False, cancelled=lambda: False):
        if sys.platform != 'win32' or not window.hwnd or not window.process_id:
            return UIAResult(warnings=('Limited context available. UI Automation unsupported.',))
        if not self._lock.acquire(blocking=False):
            return UIAResult(warnings=('Limited context available. UI Automation is busy.',))
        process = None
        ownership = None
        try:
            command = [str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                '-NoProfile', '-NonInteractive', '-MTA', '-ExecutionPolicy', 'Bypass', '-File',
                str(Path(__file__).with_name('uia_reader.ps1')), '-TargetHwnd', str(window.hwnd),
                '-TargetPid', str(window.process_id), '-IncludeUI', str(int(include_ui)),
                '-IncludeSelection', str(int(include_selection))]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            from .process import NativeProcessOwner
            ownership = NativeProcessOwner(process)
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if cancelled():
                    return UIAResult(warnings=('UI Automation capture cancelled.',))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return UIAResult(warnings=('Limited context available. UI Automation timed out.',))
                try:
                    output, _ = process.communicate(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode or len(output) > 262144:
                return UIAResult(warnings=('Limited context available. UI Automation unavailable.',))
            data = json.loads(output.decode('utf-8-sig'))
            if not isinstance(data, dict) or len(data.get('elements', ())) > 300:
                raise ValueError('Invalid UIA payload.')
            elements = []
            for item in data.get('elements', ()):
                item = dict(item)
                if item.get('bounds') is not None:
                    item['bounds'] = tuple(item['bounds'])
                elements.append(DesktopUIElement(**item))
            warnings = []
            if data.get('limited'):
                warnings.append('Limited context available. Some UI controls are unavailable.')
            if data.get('protected_focus'):
                warnings.append('Protected focused control. Suggestions are suppressed.')
            return UIAResult(tuple(elements), data.get('focused_id'), data.get('selected_text'), tuple(warnings))
        except Exception:
            # Native error text may include provider values: never log or return it.
            return UIAResult(warnings=('Limited context available. UI Automation unavailable.',))
        finally:
            if ownership:
                ownership.close()
            if process and process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
            self._lock.release()
