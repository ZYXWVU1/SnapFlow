"""Run visual classification on the existing Qt thread pool."""
from PySide6.QtCore import QObject, QRunnable, Signal
from .classifier import ScreenshotClassifier


class ClassificationSignals(QObject):
    finished = Signal(int, object)


class ClassificationWorker(QRunnable):
    def __init__(self, request_id, client, data):
        super().__init__()
        self.signals = ClassificationSignals()
        self.request_id, self.client, self.data = request_id, client, data

    def run(self):
        try:
            result = ScreenshotClassifier(self.client).classify(self.data)
        finally:
            self.data = b''
        self.signals.finished.emit(self.request_id, result)
