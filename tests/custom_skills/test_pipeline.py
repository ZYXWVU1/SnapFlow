import json
import tempfile
from pathlib import Path
import unittest
import threading
from unittest.mock import patch
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.app import ApplicationController
from src.config import Config
from src.skills.custom.storage import CustomSkillStorage
from custom_skills.helpers import sample

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class Client:
    def __init__(self, kind='unknown'):
        self.calls = []
        self.kind = kind

    def request_image(self, image, prompt, mode=''):
        self.calls.append((mode, QThread.currentThread() == APP.thread()))
        return {'': json.dumps(dict(content_type=self.kind, confidence=.95)),
                'custom_match': '{"skill_id":"receipt_tracker","confidence":0.92}',
                'skill': '{"merchant":"Shop","total":12}'}[mode]

    def analyze_image(self, image, mode, question, history=None):
        self.calls.append((mode, QThread.currentThread() == APP.thread()))
        return 'Ask answer'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CustomSkillStorage(Path(self.temp.name) / 'skills.json')
        self.store.create_skill(sample())
        with patch('src.app.load_config', return_value=Config(default_mode='smart')), patch('src.app.CustomSkillStorage', return_value=self.store):
            self.controller = ApplicationController(APP)
        self.controller.image_bytes = b'image'
        self.controller.client = Client()

    def tearDown(self):
        self.controller.discard_result()
        self.controller.pool.waitForDone()
        APP.processEvents()
        self.controller.hotkeys.close()
        self.controller.tray.hide()
        self.controller.result.close()
        self.temp.cleanup()

    def wait(self):
        for _ in range(300):
            if not self.controller.workers:
                return
            QTest.qWait(10)
        self.fail('Worker did not finish')

    def test_unknown_matches_custom_off_thread(self):
        self.controller.analyze()
        self.wait()
        self.assertEqual(self.controller.last_result.skill_id, 'receipt_tracker')
        self.assertEqual([x[0] for x in self.controller.client.calls], ['', 'custom_match', 'skill'])
        self.assertFalse(any(x[1] for x in self.controller.client.calls))

    def test_disabled_skips_match(self):
        self.store.set_enabled('receipt_tracker', False)
        self.controller.analyze()
        self.wait()
        self.assertEqual([x[0] for x in self.controller.client.calls], ['', 'ask'])

    def test_builtin_priority(self):
        self.controller.client.kind = 'assignment'
        self.controller.analyze()
        self.wait()
        self.assertEqual(self.controller.last_result.skill_id, 'assignment')
        self.assertEqual([x[0] for x in self.controller.client.calls], ['', 'skill'])

    def test_close_during_custom_match_ignores_late_response(self):
        started, release = threading.Event(), threading.Event()
        original = self.controller.client.request_image
        def delayed(*args, **kwargs):
            if kwargs.get('mode') == 'custom_match':
                started.set()
                release.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.controller.client, 'request_image', side_effect=delayed):
            self.controller.analyze()
            for _ in range(100):
                QTest.qWait(10)
                if started.is_set():
                    break
            self.assertTrue(started.is_set())
            self.controller.result.close()
            release.set()
            self.wait()
        self.assertIsNone(self.controller.last_result)
        self.assertEqual([x[0] for x in self.controller.client.calls], ['', 'custom_match'])

    def test_custom_extraction_error_retries_same_skill(self):
        original = self.controller.client.request_image
        def malformed(*args, **kwargs):
            return 'broken' if kwargs.get('mode') == 'skill' else original(*args, **kwargs)
        with patch.object(self.controller.client, 'request_image', side_effect=malformed):
            self.controller.analyze()
            self.wait()
        self.assertIn('Ask AI', self.controller.result.action_buttons)
        self.assertIsNone(self.controller.last_result)
        self.controller.analyze()
        self.wait()
        self.assertEqual(self.controller.last_result.skill_id, 'receipt_tracker')
        self.assertEqual([x[0] for x in self.controller.client.calls], ['', 'custom_match', 'skill'])

    def test_quit_waits_for_teaching_pool_without_blocking_ui(self):
        with patch('src.app.QThreadPool.globalInstance') as pool, patch.object(self.controller.app, 'quit') as quit_app:
            pool.return_value.activeThreadCount.return_value = 1
            self.controller.finish_quit()
            quit_app.assert_not_called()
            pool.return_value.activeThreadCount.return_value = 0
            self.controller.finish_quit()
            quit_app.assert_called_once()
