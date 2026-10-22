"""Qt lifecycle owner. Explicit opt-in; authority exists only in RAM."""
from PySide6.QtCore import QObject, QTimer, Signal, Qt

from src.mcp.server.ipc import LocalIPCServer
from src.reliability.policy import get_safety_policy
from .manager import BrowserManager


class BrowserCompanionService(QObject):
    changed = Signal()
    backend_changed = Signal()
    question_requested = Signal(str)

    def __init__(self, paths, credentials, parent=None, *, safety_policy=None):
        super().__init__(parent)
        self.paths, self.credentials = paths, credentials
        self.policy = safety_policy or get_safety_policy()
        self.manager = self.ipc = None
        self.warning = ''
        self.question_runtime = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self._last = ()
        self.backend_changed.connect(self.refresh, Qt.ConnectionType.QueuedConnection)

    @property
    def enabled(self):
        return self.manager is not None and self.manager.enabled

    def enable(self, allowed_origins):
        self.policy.require('browser_companion')
        from .permissions import BrowserSitePermissions
        manager = BrowserManager(allowed_origins, safety_policy=self.policy,
            site_permissions=BrowserSitePermissions(self.paths.data_dir / 'browser_site_permissions.json'),
            changed=self.backend_changed.emit)
        if self.question_runtime:
            manager.set_question_runtime(self.question_runtime, self.question_requested.emit)
        self.close()
        ipc = LocalIPCServer(self.paths, self.credentials, manager, self, namespace='browser')
        try:
            manager.enable()
            ipc.start()
        except Exception:
            manager.disable()
            ipc.close()
            ipc.deleteLater()
            raise
        self.manager, self.ipc = manager, ipc
        self.warning = ''
        self.timer.start()
        self.changed.emit()

    def connections(self):
        return self.manager.connections() if self.manager else ()

    def sessions(self):
        return self.manager.sessions() if self.manager else ()

    @property
    def context(self):
        return self.manager.context if self.manager else None

    def refresh(self):
        if not self.policy.allows('browser_companion'):
            self.close()
            return
        if self.manager:
            self.manager.questions.prune()
        current = (tuple((c.connection_id, c.browser_kind) for c in self.connections()),
                   tuple(s.session_id for s in self.sessions()),
                   tuple(s.snapshot_id for s in self.context.snapshots()) if self.context else (),
                   self.manager.questions.signature() if self.manager else (),
                   self.manager.site_permissions.entries() if self.manager else ())
        if current != self._last:
            self._last = current
            self.changed.emit()

    def close(self):
        self.timer.stop()
        if self.manager:
            self.manager.disable()
        if self.ipc:
            self.ipc.close()
            self.ipc.deleteLater()
        self.manager = self.ipc = None
        self._last = ()
        self.changed.emit()
