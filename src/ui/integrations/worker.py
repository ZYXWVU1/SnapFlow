"""Run connection operations away from the Qt UI thread."""
from PySide6.QtCore import QObject, QRunnable, Signal

from src.integrations.service import ConnectionOutcome
from src.skill_actions import ActionResult


class ConnectionSignals(QObject):
    finished = Signal(str, object)


class ConnectionWorker(QRunnable):
    def __init__(self, service, integration_id, operation, *args):
        super().__init__()
        if operation not in ('connect_google', 'connect_todoist', 'test_connection', 'disconnect'):
            raise ValueError('Unknown integration operation.')
        self.service, self.integration_id, self.operation = service, integration_id, operation
        self.args = args
        self.signals = ConnectionSignals()

    def run(self):
        try:
            outcome = getattr(self.service, self.operation)(*self.args)
        except Exception:
            outcome = ConnectionOutcome(False, 'error', 'Unable to complete the integration operation.')
        self.signals.finished.emit(self.integration_id, outcome)


class CalendarEventSignals(QObject):
    finished = Signal(object)


class CalendarEventWorker(QRunnable):
    def __init__(self, service, result):
        super().__init__()
        self.service, self.result = service, result
        self.signals = CalendarEventSignals()

    def run(self):
        try:
            outcome = self.service.create_detected_calendar_event(self.result)
        except Exception:
            outcome = ActionResult(False, 'Google Calendar could not complete the event.')
        self.signals.finished.emit(outcome)
