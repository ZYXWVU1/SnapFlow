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

    def execute(self, kind, first, second, *, execution_guard=None):
        if QThread.currentThread() == self.thread():
            try:
                if execution_guard is not None and not execution_guard():
                    return ActionResult(False, 'Workflow request revoked.')
                return self._perform(kind, first, second)
            except Exception:
                return ActionResult(False, 'Unable to perform the UI Action.')
        request = {'kind': kind, 'first': first, 'second': second, 'event': Event(),
                   'result': None, 'expired': False, 'lock': Lock(), 'guard': execution_guard}
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
                        if request['guard'] is not None and not request['guard']():
                            request['result'] = ActionResult(False, 'Workflow request revoked.')
                        else:
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
                 integration_service=None, mcp_approval=None, execution_guard=None):
        super().__init__()
        self.definition, self.context, self.executor = definition, context, executor
        self.bridge, self.preview = bridge, preview
        self.storage = storage
        self.integration_service = integration_service
        self.mcp_approval = mcp_approval
        self.execution_guard = execution_guard
        self.cancel = Event()
        self.signals = WorkflowSignals()

    def run(self):
        try:
            def current():
                return not self.cancel.is_set() and (self.storage is None or
                    self.storage.get_workflow(self.definition.id) == self.definition) and (self.execution_guard is None or self.execution_guard())
            ui_action = (lambda kind, first, second: self.bridge.execute(kind, first, second, execution_guard=current)) if self.execution_guard else self.bridge.execute
            effects = WorkflowEffects(ui_action, self.integration_service, self.mcp_approval,
                cancel=self.cancel, execution_guard=current)
            def guarded(action, source, config):
                if not current():
                    return ActionResult(False, 'Workflow changed or disabled before this Action.')
                return effects.execute(action, source, config)
            self.executor.execute_action = guarded
            current_definition = self.storage.get_workflow(self.definition.id) if self.storage is not None else self.definition
            definition = self.definition if current_definition == self.definition else replace(self.definition, enabled=False)
            result = self.executor.execute(definition, self.context, cancel=self.cancel, preview=self.preview)
        except Exception as error:
            from src.reliability.crashes import record_background_error
            record_background_error(error)
            result = None
        self.signals.finished.emit(self, result)
