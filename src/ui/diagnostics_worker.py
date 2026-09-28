"""Run local health checks away from the Qt UI thread."""
import logging

from PySide6.QtCore import QObject, QRunnable, Signal


class DiagnosticsWorkerSignals(QObject):
    finished = Signal(object)


class BasicHealthCheckWorker(QRunnable):
    def __init__(self, diagnostics_service):
        super().__init__()
        self.diagnostics_service = diagnostics_service
        self.signals = DiagnosticsWorkerSignals()

    def run(self):
        try:
            result = self.diagnostics_service.run_basic_health_check()
        except Exception:
            logging.getLogger(__name__).warning('Basic health check failed unexpectedly.', exc_info=True)
            result = None
        self.signals.finished.emit(result)
