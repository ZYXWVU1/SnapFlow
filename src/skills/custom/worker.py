"""Custom matching completion follows the controller's request identity contract."""
from PySide6.QtCore import QRunnable
from src.smart.worker import ClassificationSignals
from .matcher import CustomSkillMatcher


class CustomMatchWorker(QRunnable):
    def __init__(self, request_id, client, data, definitions):
        super().__init__()
        self.signals = ClassificationSignals()
        self.request_id, self.client, self.data = request_id, client, data
        self.definitions = tuple(definitions)

    def run(self):
        try:
            result = CustomSkillMatcher(self.client).match(self.data, self.definitions)
        finally:
            self.data = b''
        self.signals.finished.emit(self.request_id, result)
