"""Win32 foreground metadata. No OCR, input injection or privilege changes."""
import ctypes
from ctypes import wintypes as w
from pathlib import PureWindowsPath
import sys

from .models import ActiveWindowContext


def classify(process):
    name = (process or '').casefold()
    groups = {'browser': ('chrome', 'msedge', 'firefox', 'brave', 'opera'),
        'code_editor': ('code', 'devenv', 'pycharm', 'notepad++'),
        'terminal': ('windowsterminal', 'powershell', 'pwsh', 'cmd'),
        'office': ('winword', 'excel', 'powerpnt', 'soffice', 'notepad'),
        'chat': ('discord', 'slack', 'teams'), 'file_manager': ('explorer',),
        'media': ('vlc', 'wmplayer')}
    stem = PureWindowsPath(name).stem
    return next((category for category, names in groups.items() if stem in names), 'other')


class ActiveWindowProvider:
    def __init__(self, *, title_allowed=lambda app: True):
        self.title_allowed = title_allowed

    def __call__(self):
        if sys.platform != 'win32':
            return ActiveWindowContext()
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        user.GetForegroundWindow.restype = w.HWND
        user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
        user.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
        user.SendMessageTimeoutW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t))
        user.SendMessageTimeoutW.restype = w.LPARAM
        kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        kernel.OpenProcess.restype = w.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
        kernel.CloseHandle.argtypes = (w.HANDLE,)
        hwnd = user.GetForegroundWindow()
        if not hwnd:
            return ActiveWindowContext()
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = None
        handle = kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if handle:
            try:
                buffer, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    process = PureWindowsPath(buffer.value).name
            finally:
                kernel.CloseHandle(handle)
        title = None
        if process and self.title_allowed(process):
            buffer, result = ctypes.create_unicode_buffer(501), ctypes.c_size_t()
            # WM_GETTEXT, SMTO_ABORTIFHUNG; no unbounded target message waits.
            if user.SendMessageTimeoutW(hwnd, 0x000D, 501, ctypes.addressof(buffer), 2, 100, ctypes.byref(result)):
                title = buffer.value
        rect = w.RECT()
        bounds = (rect.left, rect.top, rect.right, rect.bottom) if user.GetWindowRect(hwnd, ctypes.byref(rect)) else None
        fullscreen = False
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('monitor', w.RECT), ('work', w.RECT), ('flags', w.DWORD)]
        user.MonitorFromWindow.argtypes = (w.HWND, w.DWORD)
        user.MonitorFromWindow.restype = w.HANDLE
        user.GetMonitorInfoW.argtypes = (w.HANDLE, ctypes.POINTER(MonitorInfo))
        monitor = user.MonitorFromWindow(hwnd, 2)
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if bounds and monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            screen = info.monitor
            fullscreen = bounds[0] <= screen.left and bounds[1] <= screen.top and bounds[2] >= screen.right and bounds[3] >= screen.bottom
        return ActiveWindowContext(int(hwnd), pid.value or None, process,
            PureWindowsPath(process).stem if process else None, title, bounds,
            category=classify(process), fullscreen=fullscreen)
