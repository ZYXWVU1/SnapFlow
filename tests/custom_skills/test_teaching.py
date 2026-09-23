import json
import threading
import unittest
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from custom_skills.helpers import sample
from src.ui.skills.teach_by_example import TeachByExample
from src.ui.skills.skill_test_dialog import SkillTestDialog

APP = QApplication.instance() or QApplication([])


class Client:
    def __init__(self, confidence=.92):
        self.confidence = confidence
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def request_image(self, image, prompt, mode=''):
        self.calls.append((mode, QThread.currentThread() == APP.thread()))
        self.release.wait(3)
        if mode == 'custom_generate':
            return json.dumps(sample().to_dict())
        if mode == 'custom_match':
            return json.dumps(dict(skill_id='receipt_tracker', confidence=self.confidence))
        return '{"merchant":"Shop","total":20}'


class TeachingTests(unittest.TestCase):
    def wait(self, dialog):
        for _ in range(400):
            if not dialog.busy:
                return
            QTest.qWait(10)
        self.fail('Dialog did not finish')

    def test_generation_delivers_reviewable_draft_off_thread(self):
        client = Client()
        dialog = TeachByExample(client)
        self.addCleanup(dialog.close)
        dialog.image_bytes = b'image'
        dialog.purpose.setPlainText('Track receipts')
        drafts = []
        dialog.generated.connect(drafts.append)
        dialog.generate()
        self.wait(dialog)
        self.assertEqual(len(drafts), 1)
        self.assertFalse(drafts[0].enabled)
        self.assertFalse(client.calls[0][1])

    def test_low_confidence_does_not_extract(self):
        client = Client(.31)
        dialog = SkillTestDialog(sample(), client)
        self.addCleanup(dialog.close)
        dialog.image_bytes = b'image'
        dialog.run_test()
        self.wait(dialog)
        self.assertIn('31%', dialog.status.text())
        self.assertIn('probably does not match', dialog.status.text())
        self.assertEqual([c[0] for c in client.calls], ['custom_match'])

    def test_test_disabled_draft_and_close_ignores_late_result(self):
        client = Client()
        dialog = SkillTestDialog(sample(enabled=False), client)
        dialog.image_bytes = b'image'
        dialog.run_test()
        self.wait(dialog)
        self.assertIn('Merchant', dialog.result.section_titles)
        self.assertFalse(any(c[1] for c in client.calls))
        dialog.close()
        teach = TeachByExample(client)
        teach.image_bytes = b'image'
        teach.purpose.setPlainText('Track receipts')
        drafts = []
        teach.generated.connect(drafts.append)
        client.release.clear()
        teach.generate()
        teach.close()
        client.release.set()
        self.wait(teach)
        self.assertEqual(drafts, [])
        self.assertEqual(teach.image_bytes, b'')
