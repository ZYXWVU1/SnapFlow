"""Topmost windows yield their z-order when another window gets focus."""
import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QEvent, Qt


_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200


def set_native_topmost(window, enabled):
    """Update native z-order without activating or moving the window."""
    if sys.platform == "win32":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        set_window_pos.restype = wintypes.BOOL
        hwnd = wintypes.HWND(int(window.winId()))
        insert_after = wintypes.HWND(-1 if enabled else -2)
        result = set_window_pos(
            hwnd, insert_after, 0, 0, 0, 0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER,
        )
        if not result:
            logging.getLogger(__name__).warning(
                "Unable to update a window's topmost state (Win32 error %s).",
                ctypes.get_last_error(),
            )
        return bool(result)

    was_visible = window.isVisible()
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(enabled))
    if was_visible:
        window.show()
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
    return True


class FocusAwareTopmostMixin:
    """Keep opted-in windows topmost only while they are the active window."""

    def configure_focus_topmost(self, enabled):
        self._focus_topmost_enabled = bool(enabled)
        if sys.platform == "win32":
            # On Windows the native z-order is managed on activation events.
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._focus_topmost_enabled)

    def set_focus_topmost_enabled(self, enabled):
        self._focus_topmost_enabled = bool(enabled)
        set_native_topmost(self, self._focus_topmost_enabled and self.isActiveWindow())

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.WindowActivate:
            set_native_topmost(self, self._focus_topmost_enabled)
        elif event.type() == QEvent.Type.WindowDeactivate:
            set_native_topmost(self, False)
        return handled
