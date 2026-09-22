"""Extraction jobs with the same completion contract as manual analysis."""
from PySide6.QtCore import QObject, QRunnable, Signal
from src.llm_client import AnalysisError
from src.modes import ResponseFormatError


class SkillSignals(QObject):
    finished = Signal(int, object, bool)


class SkillExtractionWorker(QRunnable):
    def __init__(self, request_id, client, data, skill, confidence):
        super().__init__()
        self.signals = SkillSignals()
        self.request_id, self.client, self.data = request_id, client, data
        self.skill, self.confidence = skill, confidence
        self.question = ''

    def run(self):
        try:
            result, error = self.skill.extract(self.client, self.data, self.confidence), False
        except (AnalysisError, ResponseFormatError) as exc:
            result, error = str(exc), True
        except Exception:
            result, error = 'Unable to extract reliable details. Retry or Ask AI about this screenshot.', True
        finally:
            self.data = b''
        self.signals.finished.emit(self.request_id, result, error)
