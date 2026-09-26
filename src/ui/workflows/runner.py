"""Qt-safe workflow execution and main-thread UI effects."""
from dataclasses import replace
from threading import Event, Lock
from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot, QThread
from PySide6.QtWidgets import QApplication
from src.skill_actions import ActionResult
from src.workflows.effects import WorkflowEffects


class WorkflowBridge(QObject):
    requested = Signal(object)

    def __init__(self, tray, ask_callback, parent=None, timeout_seconds=30):
        super().__init__(parent)
        self.tray = tray
        self.ask_callback = ask_callback
        self.timeout_seconds = timeout_seconds
        self.requested.connect(self._apply, Qt.ConnectionType.QueuedConnection)

    def execute(self, kind, first, second):
        if QThread.currentThread() == self.thread():
            try:
                return self._perform(kind, first, second)
            except Exception:
                return ActionResult(False, 'Unable to perform the UI Action.')
        request = {'kind': kind, 'first': first, 'second': second, 'event': Event(),
                   'result': None, 'expired': False, 'lock': Lock()}
        self.requested.emit(request)
        if not request['event'].wait(self.timeout_seconds):
            with request['lock']:
                if request['result'] is None:
                    request['expired'] = True
                    return ActionResult(False, 'UI action timed out.')
        return request['result']

    @Slot(object)
    def _apply(self, request):
        try:
            with request['lock']:
                if not request['expired']:
                    try:
                        request['result'] = self._perform(request['kind'], request['first'], request['second'])
                    except Exception:
                        request['result'] = ActionResult(False, 'Unable to perform the UI Action.')
        finally:
            request['event'].set()

    def _perform(self, kind, first, second):
        if kind == 'copy':
            QApplication.clipboard().setText(first)
            return ActionResult(True, 'Copied to clipboard.')
        if kind == 'notification':
            self.tray.showMessage(first, second)
            return ActionResult(True, 'Notification shown.')
        if kind == 'ask':
            self.ask_callback(first)
            return ActionResult(True, 'Ask AI opened.')
        return ActionResult(False, 'Unsupported UI action.')


class WorkflowSignals(QObject):
    finished = Signal(object, object)


class WorkflowWorker(QRunnable):
    def __init__(self, definition, context, executor, bridge, *, preview=False, storage=None,
                 integration_service=None):
        super().__init__()
        self.definition, self.context, self.executor = definition, context, executor
        self.bridge, self.preview = bridge, preview
        self.storage = storage
        self.integration_service = integration_service
        self.cancel = Event()
        self.signals = WorkflowSignals()

    def run(self):
        try:
            effects = WorkflowEffects(self.bridge.execute, self.integration_service)
            def guarded(action, source, config):
                if self.storage is not None and self.storage.get_workflow(self.definition.id) != self.definition:
                    return ActionResult(False, 'Workflow changed or disabled before this Action.')
                return effects.execute(action, source, config)
            self.executor.execute_action = guarded
            current = self.storage.get_workflow(self.definition.id) if self.storage is not None else self.definition
            definition = self.definition if current == self.definition else replace(self.definition, enabled=False)
            result = self.executor.execute(definition, self.context, cancel=self.cancel, preview=self.preview)
        except Exception:
            result = None
        self.signals.finished.emit(self, result)
