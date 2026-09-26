"""Read-only provider resource discovery away from the UI thread."""
from PySide6.QtCore import QObject, QRunnable, Signal


class ResourceSignals(QObject):
    finished = Signal(object, object, str)


class ResourceWorker(QRunnable):
    def __init__(self, service, action_id, config):
        super().__init__()
        self.service, self.action_id, self.config = service, action_id, config
        self.signals = ResourceSignals()

    def run(self):
        try:
            options = self.service.list_resources(self.action_id, self.config)
            error = ''
        except ValueError as exc:
            options, error = [], str(exc)
        except Exception:
            options, error = [], 'Unable to load resources. Check the connection and try again.'
        self.signals.finished.emit(self, options, error)
