"""Main-app ownership of server policy, local IPC and Workflow approval."""
from threading import Event
import json
import time
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox
from src.mcp.server.policy import ServerPolicyStore
from src.mcp.server.adapters import SnapFlowServiceAdapter
from src.mcp.server.ipc import LocalIPCServer
from src.ui.workflows.runner import WorkflowWorker
from src.workflows.executor import WorkflowExecutor


class MCPServerService(QObject):
    changed = Signal()
    approval_requested = Signal(object)
    execution_requested = Signal(object)

    def __init__(self, paths, credentials, memory, skills, workflows, history, workflow_pool,
                 integration_service=None, extension_service=None, parent=None):
        super().__init__(parent)
        self.policy = ServerPolicyStore(paths.data_dir / 'mcp_server.json')
        self.adapter = SnapFlowServiceAdapter(self.policy, memory, skills, workflows, history)
        self.adapter.approve_workflow, self.adapter.execute_workflow = self.approve, self.execute
        self.ipc = LocalIPCServer(paths, credentials, self.adapter, self)
        self.workflow_pool = workflow_pool
        self.integration_service, self.extension_service = integration_service, extension_service
        self.approval_parent = self.workflow_bridge = None
        self.shutting_down = False
        self.warning = self.policy.warning
        self.jobs = {}
        self.pending = []
        self.active_dialog = None
        self.approval_requested.connect(self._approve, Qt.ConnectionType.QueuedConnection)
        self.execution_requested.connect(self._execute, Qt.ConnectionType.QueuedConnection)

    @property
    def active_workflows(self):
        return len(self.jobs)

    def start(self):
        if self.policy.snapshot().enabled:
            try:
                self.ipc.start()
                self.warning = ''
            except Exception:
                self.warning = 'Local MCP bridge unavailable. Check Windows credential storage and retry Apply.'
        self.changed.emit()

    def configure(self, **flags):
        self.policy.update(**flags)
        if self.policy.snapshot().enabled:
            self.start()
            if not self.ipc.listener.isListening():
                self.policy.update(enabled=False)
        else:
            self.ipc.close()
            for worker in self.jobs.values():
                worker.cancel.set()
        self.changed.emit()

    def close(self):
        self.shutting_down = True
        for worker in self.jobs.values():
            worker.cancel.set()
        for request in list(self.pending):
            request['cancel'].set()
        if self.active_dialog:
            self.active_dialog.reject()
        self.ipc.close()

    def _wait(self, request, seconds=60):
        deadline = time.monotonic() + seconds
        while not request['done'].wait(.05):
            if self.shutting_down or request['cancel'].is_set() or not request['guard']() or time.monotonic() >= deadline:
                request['cancel'].set()
                raise PermissionError('Approval Required / Unavailable or request cancelled.')
        if request['cancel'].is_set():
            raise PermissionError('Request cancelled.')
        return request.get('result')

    def approve(self, preview, cancel, guard):
        if self.approval_parent is None or self.shutting_down:
            return False
        request = {'preview': preview, 'cancel': cancel, 'guard': guard, 'done': Event(), 'result': False}
        self.approval_requested.emit(request)
        return self._wait(request)

    @Slot(object)
    def _approve(self, request):
        self.pending.append(request)
        box = timer = None
        try:
            if self.approval_parent is None or self.shutting_down or request['cancel'].is_set() or not request['guard']() or self.active_dialog:
                return
            box = QMessageBox(self.approval_parent)
            self.active_dialog = box
            box.setWindowTitle('External MCP Workflow request')
            box.setTextFormat(Qt.TextFormat.PlainText)
            preview = request['preview']
            steps = '\n'.join(f"• {s['label']} ({s['risk']})" for s in preview['steps'] if s['status'] == 'success')
            box.setText(f"An external MCP host requests this Workflow:\n{preview['workflow_name']}\n\n{steps}\n\nAllow this exact Workflow and supplied context once?")
            box.setDetailedText(json.dumps(preview, ensure_ascii=False, indent=2))
            yes = box.addButton('Allow Once', QMessageBox.ButtonRole.YesRole)
            no = box.addButton('Reject', QMessageBox.ButtonRole.NoRole)
            box.setDefaultButton(no)
            box.setEscapeButton(no)
            timer = QTimer(box)
            timer.setInterval(50)
            timer.timeout.connect(lambda: box.reject() if self.shutting_down or request['cancel'].is_set() or not request['guard']() else None)
            timer.start()
            box.exec()
            request['result'] = box.clickedButton() == yes and not request['cancel'].is_set() and request['guard']()
        finally:
            if timer is not None:
                timer.stop()
            if box is not None:
                box.deleteLater()
            self.active_dialog = None
            self.pending.remove(request)
            request['done'].set()

    def execute(self, workflow, context, cancel, guard):
        if self.workflow_bridge is None or self.shutting_down:
            raise PermissionError('Workflow UI unavailable.')
        request = {'workflow': workflow, 'context': context, 'cancel': cancel, 'guard': guard, 'done': Event()}
        self.execution_requested.emit(request)
        result = self._wait(request, 120)
        if result is None:
            raise ValueError('Workflow execution unavailable.')
        return result

    @Slot(object)
    def _execute(self, request):
        if self.shutting_down or request['cancel'].is_set() or not request['guard']():
            request['done'].set()
            return
        worker = WorkflowWorker(request['workflow'], request['context'], WorkflowExecutor(self.adapter.skills),
            self.workflow_bridge, storage=self.adapter.workflows, integration_service=self.integration_service,
            mcp_approval=self.extension_service.approve if self.extension_service else None,
            execution_guard=request['guard'])
        worker.cancel = request['cancel']
        self.jobs[id(worker)] = worker
        def finished(current, result):
            self.jobs.pop(id(current), None)
            if result is not None:
                try:
                    self.adapter.history.record(request['workflow'], result, request['context'].skill_id)
                except OSError:
                    pass
            request['result'] = result
            request['done'].set()
        worker.signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.workflow_pool.start(worker)
