import json
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.app import ApplicationController
from src.config import Config
from src.llm_client import AnalysisError
from src.smart.models import ClassificationResult
from src.ui.settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class FakeClient:
    def __init__(self, kind='table', confidence=.95, failure=False):
        self.kind, self.confidence, self.failure = kind, confidence, failure
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def request_image(self, image, prompt, mode=''):
        if mode == 'skill':
            self.calls.append((self.kind, image, QThread.currentThread() == APP.thread()))
            return json.dumps({
                'assignment': dict(course='CS 220', title='Homework', due_date='2026-09-28'),
                'event': dict(title='Club', date='2026-09-24', meeting_url='https://zoom.us/j/123'),
                'code_error': dict(error_type='IndexError', likely_cause='Bad index', evidence=['a[9]']),
                'table': dict(headers=['A'], rows=[['42']]),
            }[self.kind])
        self.calls.append(('classify', image, QThread.currentThread() == APP.thread()))
        self.started.set()
        self.release.wait(5)
        if self.failure:
            raise RuntimeError('private failure')
        return json.dumps(dict(content_type=self.kind, confidence=self.confidence))

    def analyze_image(self, image, mode, question, history=None):
        self.calls.append((mode, image, QThread.currentThread() == APP.thread()))
        if mode == 'debug':
            return json.dumps(dict(error_type='IndexError', language='Python', summary='Bad index',
                                   evidence=['a[9]'], root_cause='Two elements', fixes=[], confidence='high'))
        if mode == 'extract':
            return '{"content_type":"table","headers":["A"],"rows":[[42]]}'
        return 'Ask answer'


class SmartUITests(unittest.TestCase):
    def setUp(self):
        with patch('src.app.load_config', return_value=Config(default_mode='smart')):
            self.controller = ApplicationController(APP)
        self.controller.image_bytes = b'original png bytes'
        self.controller.result.show()

    def tearDown(self):
        client = self.controller.client
        if isinstance(client, FakeClient):
            client.release.set()
        self.controller.discard_result()
        self.controller.pool.waitForDone()
        APP.processEvents()
        self.controller.hotkeys.close()
        self.controller.tray.hide()
        self.controller.result.close()

    def wait_for(self):
        for _ in range(200):
            if not self.controller.workers:
                return
            QTest.qWait(10)
        self.fail('Worker did not complete')

    def test_routes_reuse_workflows_and_screenshot_off_main_thread(self):
        for kind, mode in [('code_error', 'code_error'), ('table', 'table'), ('unknown', 'ask')]:
            with self.subTest(kind=kind):
                client = self.controller.client = FakeClient(kind)
                self.controller.analyze()
                self.assertIn('Understanding screenshot', self.controller.result.text.toPlainText())
                self.wait_for()
                self.assertEqual([c[0] for c in client.calls], ['classify', mode])
                self.assertTrue(all(c[1] == b'original png bytes' and not c[2] for c in client.calls))
                self.assertEqual(getattr(self.controller.last_result, 'skill_id', getattr(self.controller.last_result, 'mode', None)), mode)
                self.assertEqual(self.controller.mode, 'smart')
                self.assertIn('Confidence: 95%', self.controller.result.notice.text())

    def test_skill_results_and_ask_ai(self):
        for kind in ('assignment', 'event'):
            self.controller.change_mode('smart', analyze=False)
            client = self.controller.client = FakeClient(kind)
            self.controller.analyze()
            self.wait_for()
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(self.controller.last_result.skill_id, kind)
            self.assertTrue(self.controller.result.structured.isVisible())
            self.controller.result.action_buttons['Ask AI'].click()
            self.wait_for()
            self.assertEqual(client.calls[-1][0], 'ask')
            self.assertEqual(self.controller.last_result.mode, 'ask')

    def test_low_confidence_and_api_failure_fall_back(self):
        for client in (FakeClient('table', .4), FakeClient(failure=True)):
            self.controller.client = client
            self.controller.analyze()
            self.wait_for()
            self.assertEqual(client.calls[-1][0], 'ask')
            self.assertIn('Opening Ask', self.controller.result.notice.text())

    def test_close_during_classification_does_not_dispatch_or_reopen(self):
        client = self.controller.client = FakeClient()
        client.release.clear()
        self.controller.analyze()
        self.assertTrue(client.started.wait(1))
        self.controller.result.close()
        client.release.set()
        self.wait_for()
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.controller.result.isVisible())
        self.assertIsNone(self.controller.last_result)

    def test_stale_classification_does_not_overwrite_new_request(self):
        client = self.controller.client = FakeClient()
        client.release.clear()
        self.controller.analyze()
        old_id = self.controller.request_id
        self.assertTrue(client.started.wait(1))
        self.controller.discard_result()
        self.controller.image_bytes = b'new png'
        self.controller.result.set_response('New capture')
        client.release.set()
        self.wait_for()
        self.controller.classification_finished(old_id, ClassificationResult('table', .99))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.controller.result.text.toPlainText(), 'New capture')

    def test_ask_followup_does_not_reclassify(self):
        client = self.controller.client = FakeClient('unknown')
        self.controller.analyze()
        self.wait_for()
        self.controller.analyze('Why?')
        self.wait_for()
        self.assertEqual([c[0] for c in client.calls], ['classify', 'ask', 'ask'])

    def test_settings_preserve_internal_threshold(self):
        window = SettingsWindow(Config(smart_classification_threshold=.9))
        results = []
        window.submitted.connect(lambda config, key: results.append(config))
        window.submit()
        self.assertEqual(results[0].smart_classification_threshold, .9)
        window.close()

    def test_routing_exception_falls_back_to_ask(self):
        client = self.controller.client = FakeClient()
        with patch('src.app.SmartRouter.route', side_effect=RuntimeError('private text')):
            self.controller.analyze()
            self.wait_for()
        self.assertEqual([c[0] for c in client.calls], ['classify', 'ask'])
        self.assertIn('Opening Ask', self.controller.result.notice.text())

    def test_workflow_failure_uses_normal_error_ui(self):
        self.controller.client = FakeClient('unknown')
        with patch.object(self.controller.client, 'analyze_image', side_effect=AnalysisError('Service unavailable')):
            self.controller.analyze()
            self.wait_for()
        self.assertEqual(self.controller.result.text.toPlainText(), 'Service unavailable')
        self.assertFalse(self.controller.result.copy.isEnabled())
        self.assertIsNone(self.controller.last_result)

    def test_close_during_execution_ignores_late_result(self):
        client = self.controller.client = FakeClient('table')
        started, release = threading.Event(), threading.Event()
        original = client.request_image
        def delayed(*args, **kwargs):
            if kwargs.get('mode') == 'skill':
                started.set()
                release.wait(5)
            return original(*args, **kwargs)
        try:
            with patch.object(client, 'request_image', side_effect=delayed):
                self.controller.analyze()
                for _ in range(100):
                    if started.is_set():
                        break
                    QTest.qWait(10)
                self.assertTrue(started.is_set())
                self.assertIn('Extracting data', self.controller.result.text.toPlainText())
                self.controller.result.close()
                release.set()
                self.wait_for()
            self.assertFalse(self.controller.result.isVisible())
            self.assertIsNone(self.controller.last_result)
        finally:
            release.set()

    def test_quit_during_classification_does_not_start_execution(self):
        client = self.controller.client = FakeClient()
        client.release.clear()
        with patch.object(self.controller, 'finish_quit') as finish:
            self.controller.analyze()
            self.assertTrue(client.started.wait(1))
            self.controller.quit()
            client.release.set()
            self.wait_for()
            APP.processEvents()
            self.assertEqual(len(client.calls), 1)
            finish.assert_called_once()

    def test_settings_owned_topmost_and_reactivated(self):
        self.controller.open_settings()
        window = self.controller.settings
        self.assertIs(window.parentWidget(), self.controller.result)
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        with patch.object(window, 'raise_') as raised, patch.object(window, 'activateWindow') as activated:
            self.controller.open_settings()
            raised.assert_called_once()
            activated.assert_called_once()
        window.close()

    def test_skill_failure_has_ask_and_retry(self):
        client = self.controller.client = FakeClient('assignment')
        original = client.request_image
        def malformed(*args, **kwargs):
            return 'not json' if kwargs.get('mode') == 'skill' else original(*args, **kwargs)
        with patch.object(client, 'request_image', side_effect=malformed):
            self.controller.analyze()
            self.wait_for()
        self.assertIsNone(self.controller.last_result)
        self.assertIn('Ask AI', self.controller.result.action_buttons)
        self.assertTrue(self.controller.result.again.isEnabled())
        self.assertIn('could not reliably extract', self.controller.result.text.toPlainText())

    def test_skill_copy_and_contextual_followup(self):
        client = self.controller.client = FakeClient('assignment')
        self.controller.analyze()
        self.wait_for()
        self.controller.result.action_buttons['Copy Details'].click()
        self.assertIn('Homework', APP.clipboard().text())
        with patch.object(client, 'analyze_image', wraps=client.analyze_image) as analyze:
            self.controller.result.action_buttons['Ask AI'].click()
            self.wait_for()
        history = analyze.call_args.kwargs['history']
        self.assertIn('2026-09-28', history[-1]['content'])
        self.assertEqual(analyze.call_args.args[0], b'original png bytes')
