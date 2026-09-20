"""Windows RegisterHotKey, dispatched on Qt's GUI thread."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication

from src.config import parse_hotkey


class Hotkeys(QObject, QAbstractNativeEventFilter):
    triggered = Signal()

    def __init__(self, app: QApplication) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self.app = app
        self.active_id: int | None = None
        self.next_id = 0x4000
        self.current = ""
        if sys.platform != "win32":
            raise RuntimeError("Global hotkeys require Windows 10 or 11.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        app.installNativeEventFilter(self)

    def register(self, hotkey: str) -> None:
        if hotkey == self.current:
            return
        modifiers, key = parse_hotkey(hotkey)
        new_id = self.next_id
        if not self.user32.RegisterHotKey(None, new_id, modifiers | 0x4000, key):
            raise ValueError(f"Cannot register {hotkey}. It may be used by another app. Choose another shortcut in Settings.")
        if self.active_id is not None:
            self.user32.UnregisterHotKey(None, self.active_id)
        self.active_id = new_id
        self.next_id = 0x4001 if new_id == 0x4000 else 0x4000
        self.current = hotkey

    def nativeEventFilter(self, event_type: bytes, message: int) -> tuple[bool, int]:
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0312 and msg.wParam == self.active_id:
            self.triggered.emit()
            return True, 0
        return False, 0

    def close(self) -> None:
        if self.active_id is not None:
            self.user32.UnregisterHotKey(None, self.active_id)
            self.active_id = None
            self.current = ""
        self.app.removeNativeEventFilter(self)
