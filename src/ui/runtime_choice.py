"""Fresh native runtime choice marshalled from inference workers to the Qt thread."""
import threading
import time
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QMessageBox


class RuntimeChoiceService(QObject):
    requested = Signal(object)

    def __init__(self, parent, config_provider, policy):
        super().__init__(parent)
        self.parent_widget, self.config_provider, self.policy = parent, config_provider, policy
        self.requested.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def choose(self, request):
        pending = dict(event=threading.Event(), choice=None, request=request)
        self.requested.emit(pending)
        deadline = time.monotonic() + min(request.timeout_seconds, 120)
        while not pending['event'].wait(.05):
            if time.monotonic() >= deadline or (request.cancelled and request.cancelled()):
                break
        if not pending['event'].is_set():
            pending['event'].set()
        return pending['choice']

    @Slot(object)
    def _show(self, pending):
        if pending['event'].is_set():
            return
        config = self.config_provider()
        box = QMessageBox(self.parent_widget)
        box.setWindowTitle('Choose AI Runtime')
        box.setText('Choose where this request runs. Cloud sends the selected content to your configured provider.')
        box.setTextFormat(Qt.TextFormat.PlainText)
        local = box.addButton('Local · ' + (config.local_ai_model or 'No model configured'), QMessageBox.ButtonRole.AcceptRole)
        local.setEnabled(bool(config.local_ai_model))
        cloud = box.addButton('Cloud · ' + (config.ai_model or 'Configured model'), QMessageBox.ButtonRole.ActionRole)
        cloud.setEnabled(not (self.policy.private_mode or config.private_mode))
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        timer = QTimer(box)
        def check():
            request = pending['request']
            if pending['event'].is_set() or (request.cancelled and request.cancelled()):
                box.reject()
            if self.policy.private_mode:
                cloud.setEnabled(False)
        timer.timeout.connect(check)
        timer.start(100)
        try:
            box.exec()
            if not pending['event'].is_set():
                clicked = box.clickedButton()
                pending['choice'] = 'local_only' if clicked is local else 'cloud_only' if clicked is cloud else None
        finally:
            timer.stop()
            pending['event'].set()
            box.deleteLater()
