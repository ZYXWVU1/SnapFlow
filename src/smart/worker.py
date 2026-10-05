"""Run visual classification on the existing Qt thread pool."""
from PySide6.QtCore import QObject, QRunnable, Signal
from .classifier import ScreenshotClassifier
import time
from src.observability.performance import emit


class ClassificationSignals(QObject):
    finished = Signal(int, object)


class ClassificationWorker(QRunnable):
    def __init__(self, request_id, client, data):
        super().__init__()
        self.signals = ClassificationSignals()
        self.request_id, self.client, self.data = request_id, client, data
        self.queued_at = time.perf_counter()

    def run(self):
        emit('operation_completed', component='skill', duration_ms=(time.perf_counter() - self.queued_at) * 1000,
             success=True, properties={'operation': 'queue_wait'})
        try:
            result = ScreenshotClassifier(self.client).classify(self.data)
        finally:
            self.data = b''
        self.signals.finished.emit(self.request_id, result)
