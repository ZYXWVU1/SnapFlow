"""Run the optional provider connection test away from the Qt UI thread."""
from PySide6.QtCore import QObject, QRunnable, Signal

from src.llm_client import AnalysisError


class OnboardingConnectionSignals(QObject):
    finished = Signal(bool, str)


class OnboardingConnectionWorker(QRunnable):
    def __init__(self, client, api_key, base_url, model):
        super().__init__()
        self.client = client
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.signals = OnboardingConnectionSignals()

    def run(self):
        try:
            self.client.test_connection(api_key=self.api_key,
                                        base_url=self.base_url,
                                        model=self.model)
        except AnalysisError as exc:
            self.signals.finished.emit(False, str(exc))
        except Exception:
            self.signals.finished.emit(
                False, 'The connection test failed. Check the endpoint, model, and network.')
        else:
            self.signals.finished.emit(True, 'Connection successful. The provider may charge for this test request.')
