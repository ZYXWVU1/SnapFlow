"""Run update metadata checks away from the UI thread."""
from PySide6.QtCore import QObject, QRunnable, Signal

from src.update_checker import check_latest_release


class UpdateCheckSignals(QObject):
    finished = Signal(object, str)


class UpdateCheckWorker(QRunnable):
    def __init__(self, channel=None):
        super().__init__()
        self.channel = channel
        self.signals = UpdateCheckSignals()

    def run(self):
        try:
            self.signals.finished.emit(check_latest_release(channel=self.channel) if self.channel else check_latest_release(), '')
        except Exception:
            self.signals.finished.emit(None, 'unavailable')
