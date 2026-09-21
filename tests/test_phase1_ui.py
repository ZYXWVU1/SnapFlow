import json
import ctypes
import unittest
from ctypes import wintypes
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint, Qt
from src.ui.result_window import ResultWindow
from src.modes import parse_result
from src.app import ApplicationController
from src.config import Config
from src.llm_client import AnalysisError

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class PhaseOneUITests(unittest.TestCase):
    def test_tray_and_native_hotkey_reach_preview_capture(self):
        with patch('src.app.load_config', return_value=Config(hotkey='ctrl+alt+shift+7')):
            controller = ApplicationController(APP, preview=True)
        try:
            self.assertTrue(controller.tray.isVisible())
            controller.capture_action.trigger()
            for _ in range(50):
                if controller.overlays:
                    break
                QTest.qWait(20)
            self.assertTrue(controller.overlays)
            overlay = controller.overlays[0]
            QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
            QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(150, 150))
            self.assertTrue(controller.image_bytes.startswith(b'\x89PNG'))
            self.assertIn('No AI request', controller.result.text.toPlainText())
            self.assertFalse(controller.workers)
            message = wintypes.MSG()
            message.message = 0x0312
            message.wParam = controller.hotkeys.active_id
            controller.hotkeys.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(message))
            for _ in range(50):
                if controller.overlays:
                    break
                QTest.qWait(20)
            self.assertTrue(controller.overlays)
            QTest.keyClick(controller.overlays[0], Qt.Key.Key_Escape)
            self.assertFalse(controller.overlays)
            self.assertTrue(controller.tray.isVisible())
        finally:
            controller.hotkeys.close()
            controller.clear_overlays()
            controller.tray.hide()
            controller.result.close()

    def test_retained_modes_allow_custom_questions(self):
        for mode in ('explain', 'translate'):
            window = ResultWindow(mode, False)
            questions = []
            window.ask.connect(questions.append)
            try:
                window.set_result(parse_result(mode, 'Answer'))
                with patch('src.ui.result_window.QInputDialog.getMultiLineText', return_value=('More detail', True)):
                    window.again.click()
                self.assertEqual(questions, ['More detail'])
            finally:
                window.close()

    def test_failed_followup_retry_preserves_question_and_context(self):
        with patch('src.app.load_config', return_value=Config()):
            controller = ApplicationController(APP)
        class FakeClient:
            def __init__(self):
                self.calls = []
            def analyze_image(self, data, mode, question, history=None):
                self.calls.append((question, list(history or [])))
                if len(self.calls) == 2:
                    raise AnalysisError('Temporary failure')
                return 'Answer'
        client = FakeClient()
        controller.client = client
        try:
            image = QImage(50, 50, QImage.Format.Format_RGB32)
            image.fill(QColor('white'))
            controller.selected(image)
            self.wait_for(controller)
            controller.analyze('Why?')
            self.wait_for(controller)
            controller.result.again.click()
            self.wait_for(controller)
            self.assertEqual(client.calls[1], client.calls[2])
            controller.result.followup.setText('Old draft')
            controller.selected(image)
            self.wait_for(controller)
            self.assertEqual(controller.result.followup.text(), '')
            controller.result.followup.setText('Another draft')
            controller.result.close()
            self.assertEqual(controller.result.followup.text(), '')
        finally:
            controller.pool.waitForDone()
            controller.hotkeys.close()
            controller.tray.hide()
            controller.result.close()

    def test_table_code_and_error_reset(self):
        window = ResultWindow('extract', False)
        try:
            window.set_result(parse_result('extract', json.dumps(dict(content_type='table', headers=['A'], rows=[[42]]))))
            self.assertEqual(window.structured.table.item(0, 0).text(), '42')
            self.assertIn('Copy CSV', window.action_buttons)
            window.action_buttons['Copy CSV'].click()
            self.assertEqual(APP.clipboard().text(), 'A\r\n42\r\n')
            window.set_result(parse_result('extract', '{"content_type":"code","language":"python","code":"print(42)"}'))
            self.assertNotIn('Copy CSV', window.action_buttons)
            self.assertIn('Copy Code', window.action_buttons)
            window.set_busy()
            self.assertFalse(window.action_buttons)
            window.set_response('Invalid response', error=True)
            self.assertTrue(window.structured.isHidden())
            self.assertFalse(window.copy.isEnabled())
        finally:
            window.close()

    def test_debug_sections_and_ask_conversation(self):
        window = ResultWindow('debug', False)
        try:
            data = dict(error_type='IndexError', language='Python', summary='Bad index', evidence=['a[9]'], root_cause='Only 2 items', fixes=[], confidence='low')
            window.set_result(parse_result('debug', json.dumps(data)))
            self.assertIn('Likely Cause', window.structured.section_titles)
            self.assertNotIn('Copy Fix', window.action_buttons)
            window.set_result(parse_result('ask', '<b>Literal text</b>'))
            self.assertEqual(window.text.toPlainText(), '<b>Literal text</b>')
            self.assertTrue(window.structured.isHidden())
        finally:
            window.close()

    def test_controller_keeps_and_clears_ask_context(self):
        with patch('src.app.load_config', return_value=Config()):
            controller = ApplicationController(APP)
        class FakeClient:
            def __init__(self):
                self.histories = []
            def analyze_image(self, data, mode, question, history=None):
                self.histories.append(list(history or []))
                return 'Answer'
        client = FakeClient()
        controller.client = client
        try:
            image = QImage(50, 50, QImage.Format.Format_RGB32)
            image.fill(QColor('white'))
            controller.selected(image)
            self.wait_for(controller)
            controller.analyze('Why?')
            self.wait_for(controller)
            self.assertEqual(client.histories[-1][-1]['content'], 'Answer')
            self.assertIn('Why?', controller.result.text.toPlainText())
            controller.discard_result()
            self.assertEqual(controller.ask_history, [])
        finally:
            controller.pool.waitForDone()
            controller.hotkeys.close()
            controller.tray.hide()
            controller.result.close()

    def wait_for(self, controller):
        for _ in range(150):
            if not controller.workers:
                return
            QTest.qWait(20)
        self.fail('Worker did not finish')
