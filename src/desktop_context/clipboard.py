"""A single bounded plain-text Win32 read, only called after service consent."""
import ctypes
from ctypes import wintypes as w
import sys


def read_clipboard_text():
    if sys.platform != 'win32':
        return None
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.OpenClipboard.argtypes = (w.HWND,)
    user.IsClipboardFormatAvailable.argtypes = (w.UINT,)
    user.GetClipboardData.argtypes = (w.UINT,)
    user.GetClipboardData.restype = w.HANDLE
    kernel.GlobalSize.argtypes = (w.HANDLE,)
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalLock.argtypes = (w.HANDLE,)
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = (w.HANDLE,)
    if not user.IsClipboardFormatAvailable(13) or not user.OpenClipboard(None):
        return None
    try:
        handle = user.GetClipboardData(13)  # CF_UNICODETEXT, never clipboard history/files.
        size = kernel.GlobalSize(handle) if handle else 0
        if not 0 < size <= 131072:
            return None
        address = kernel.GlobalLock(handle)
        if not address:
            return None
        try:
            return ctypes.string_at(address, size).decode('utf-16-le', errors='replace').split('\0', 1)[0][:65536]
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()
