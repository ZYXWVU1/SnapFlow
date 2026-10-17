"""Bounded, owned Windows UIA helper. All data crosses stdin/stdout, never a shell."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Lock

from src.agent.models import thaw
from src.desktop_context.process import NativeProcessOwner
from .models import DesktopElement, DesktopElementCapabilities, DesktopState


class WindowsDesktopActionProvider:
    def __init__(self, timeout_seconds=8):
        self.timeout_seconds = max(.1, min(10, timeout_seconds))
        self.lock = Lock()
        self.last_error_code = None

    def _call(self, payload, cancelled):
        if sys.platform != 'win32':
            raise PermissionError('Windows UI Automation is unavailable.')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('UI Automation action worker is busy.')
        process, ownership = None, None
        try:
            data = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode('utf-8') + b'\n'
            if len(data) > 65536:
                raise ValueError('Native action request is too large.')
            command = [str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) /
                'System32/WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile', '-NonInteractive',
                '-MTA', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('uia_actions.ps1'))]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            ownership = NativeProcessOwner(process)
            deadline, pending = time.monotonic() + self.timeout_seconds, data
            while True:
                if cancelled():
                    raise InterruptedError('Desktop operation cancelled.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('UI Automation timed out; outcome requires review.')
                try:
                    output, _ = process.communicate(input=pending, timeout=min(.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            if len(output) > 524288:
                raise RuntimeError('UI Automation unavailable.')
            reply = json.loads(output.decode('utf-8-sig'))
            if not isinstance(reply, dict) or process.returncode or reply.get('error'):
                allowed = ('UnauthorizedAccessException', 'ElementNotAvailableException', 'InvalidOperationException',
                           'COMException', 'Win32Exception', 'ArgumentException', 'TypeInitializationException')
                self.last_error_code = reply.get('error_type') if reply.get('error_type') in allowed else 'native_unavailable'
                raise PermissionError('Native UI Automation rejected the target.')
            return reply
        finally:
            if ownership:
                ownership.close()
            if process and process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
            self.lock.release()

    def capture(self, fence, *, cancelled=lambda: False):
        raw = self._call({'operation': 'capture', 'hwnd': fence.allowed_window,
            'pid': fence.allowed_process_id}, cancelled)
        elements = []
        if raw.get('complete') is not True:
            self.last_error_code = raw.get('limit_reason') if raw.get('limit_reason') in ('budget', 'provider_unavailable') else 'incomplete_hierarchy'
            raise RuntimeError('UI hierarchy is incomplete: ' + self.last_error_code)
        if len(raw.get('elements', ())) > 300:
            raise ValueError('Native element budget exceeded.')
        for item in raw.get('elements', ()):
            item = dict(item)
            item['process_name'] = raw['process_name']
            caps = item.pop('capabilities')
            item['capabilities'] = DesktopElementCapabilities(tuple(caps['actions']), tuple(caps['patterns']))
            elements.append(DesktopElement(**item))
        return DesktopState(raw['pid'], raw['hwnd'], raw['process_name'], raw['foreground'],
            tuple(elements), raw.get('security_available') is True)

    def foreground_matches(self, fence):
        from src.desktop_context.active_window import ActiveWindowProvider
        return ActiveWindowProvider(title_allowed=lambda app: False)().hwnd == fence.allowed_window

    def execute(self, request, element, fence, *, cancelled=lambda: False):
        self._call({'operation': 'execute', 'hwnd': fence.allowed_window, 'pid': fence.allowed_process_id,
            'action': request.action_type, 'value': thaw(request.value), 'element': element.to_dict(),
            'preconditions': [c.to_dict() for c in request.expected_preconditions]}, cancelled)
        return 'uia_pattern'
