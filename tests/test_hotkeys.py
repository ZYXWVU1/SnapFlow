import ctypes
import unittest
from ctypes import wintypes
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.hotkeys import Hotkeys

APP = QApplication.instance() or QApplication([])


class HotkeyTests(unittest.TestCase):
    def test_native_dispatch_collision_and_release(self):
        first, second = Hotkeys(APP), Hotkeys(APP)
        calls = []
        first.triggered.connect(lambda: calls.append(True))
        try:
            first.register("ctrl+alt+shift+9")
            with self.assertRaises(ValueError):
                second.register("ctrl+alt+shift+9")
            msg = wintypes.MSG()
            msg.message = 0x0312
            msg.wParam = first.active_id
            self.assertEqual(first.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)), (True, 0))
            self.assertEqual(calls, [True])
            first.register("ctrl+alt+shift+8")
            second.register("ctrl+alt+shift+9")
        finally:
            first.close()
            second.close()
