import time
import unittest

from PySide6.QtCore import QThread, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.integrations.service import ConnectionOutcome
from src.ui.integrations.worker import ConnectionWorker


APP = QApplication.instance() or QApplication([])


class WorkerTests(unittest.TestCase):
    def test_connection_check_runs_off_main_thread_and_emits_result(self):
        class Service:
            def test_connection(self, integration_id):
                return ConnectionOutcome(QThread.currentThread() != APP.thread(),
                                         'connected', integration_id)
        worker = ConnectionWorker(Service(), 'todoist', 'test_connection', 'todoist')
        received = []
        worker.signals.finished.connect(lambda key, result: received.append((key, result)))
        pool = QThreadPool()
        pool.start(worker)
        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            QTest.qWait(10)
        pool.waitForDone()
        self.assertEqual(received[0][0], 'todoist')
        self.assertTrue(received[0][1].success)
