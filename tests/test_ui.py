import os
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.app import ApplicationController
from src.config import Config
from src.ui.selection_overlay import SelectionOverlay

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class UITests(unittest.TestCase):
    def test_selection_reverse_drag_and_escape(self):
        screen = APP.primaryScreen()
        image = QImage(screen.size(), QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        overlay = SelectionOverlay(screen, image)
        selected, cancelled = [], []
        overlay.selected.connect(selected.append)
        overlay.cancelled.connect(lambda: cancelled.append(True))
        overlay.show()
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(150, 150))
        QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
        self.assertEqual(len(selected), 1)
        self.assertGreater(selected[0].width(), 100)
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.assertEqual(cancelled, [True])
        overlay.close()

    def test_async_result_copy_close_and_cancel(self):
        with patch("src.app.load_config", return_value=Config()), patch.dict(os.environ, {"AI_API_KEY": ""}):
            controller = ApplicationController(APP)
        class FakeClient:
            def analyze_image(self, *args):
                time.sleep(0.08)
                return "Test answer"
        controller.client = FakeClient()
        image = QImage(50, 50, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        try:
            controller.selected(image)
            self.assertIn("Analyzing", controller.result.text.toPlainText())
            deadline = time.monotonic() + 3
            while controller.workers and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertFalse(controller.workers)
            self.assertEqual(controller.result.text.toPlainText(), "Test answer")
            controller.result.copy.click()
            self.assertEqual(APP.clipboard().text(), "Test answer")
            controller.analyze("Follow up")
            controller.result.close()
            self.assertFalse(controller.image_bytes)
            while controller.workers and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertFalse(controller.result.isVisible())
            controller.begin_selection()
            self.assertTrue(controller.overlays)
            QTest.keyClick(controller.overlays[0], Qt.Key.Key_Escape)
            self.assertFalse(controller.overlays)
            self.assertFalse(controller.workers)
        finally:
            controller.pool.waitForDone()
            controller.hotkeys.close()
            controller.clear_overlays()
            controller.tray.hide()
            controller.result.close()
