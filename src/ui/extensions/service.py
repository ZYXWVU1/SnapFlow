"""Qt queued events and fresh per-invocation user approval."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
import asyncio
import json
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox
from src.mcp.client.manager import MCPClientManager
from src.mcp.bridge.tools import register_actions


class ExtensionService(QObject):
    state_changed = Signal(object)
    completed = Signal(object)
    approval_requested = Signal(object)
    resource_selected = Signal(object)
    prompt_selected = Signal(str)
    browser_requested = Signal(object)
    interaction_requested = Signal(object)

    def __init__(self, storage, credentials=None, parent=None):
        super().__init__(parent)
        self.storage, self.credentials = storage, credentials
        self.manager = MCPClientManager(storage, credentials, on_event=self.state_changed.emit,
            open_browser=self.open_authorization, interaction_handler=self.interact)
        self.state_changed.connect(self.register, Qt.ConnectionType.QueuedConnection)
        self.approval_requested.connect(self._approval, Qt.ConnectionType.QueuedConnection)
        self.browser_requested.connect(self._browser, Qt.ConnectionType.QueuedConnection)
        self.interaction_requested.connect(self._interaction, Qt.ConnectionType.QueuedConnection)
        self.approval_parent = None
        self.shutting_down = False
        self._shutdown_pool = None
        self._active_dialog = None

    async def interact(self, request):
        if self.approval_parent is None or self.shutting_down:
            return {'action': 'cancel'}
        future = Future()
        self.interaction_requested.emit((request, future))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), 60)
        except TimeoutError:
            return {'action': 'cancel'}

    @Slot(object)
    def _interaction(self, item):
        request, future = item
        if future.done():
            return
        response = {'action': 'cancel'}
        if self.approval_parent is not None and not self.shutting_down and self._active_dialog is None:
            from .interaction_dialog import InteractionDialog
            dialog = InteractionDialog(request, self.approval_parent)
            self._active_dialog = dialog
            timer = QTimer(dialog)
            timer.setInterval(100)
            timer.timeout.connect(lambda: dialog.reject() if future.done() or self.shutting_down else None)
            timer.start()
            try:
                if dialog.exec() and not future.done():
                    response = dialog.response
            finally:
                timer.stop()
                self._active_dialog = None
                dialog.deleteLater()
        if not future.done():
            future.set_result(response)

    async def open_authorization(self, connection_id, url):
        if self.approval_parent is None or self.shutting_down:
            return False
        future = Future()
        self.browser_requested.emit({'connection_id': connection_id, 'url': url, 'future': future})
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), 60)
        except TimeoutError:
            return False

    @Slot(object)
    def _browser(self, request):
        future = request['future']
        if future.done():
            return
        accepted = False
        box = timer = None
        if self.approval_parent is not None and not self.shutting_down and self._active_dialog is None:
            from src.mcp.client.oauth import browser_url
            try:
                browser_url(request['url'])
                endpoint = urlsplit(request['url'])
                box = QMessageBox(self.approval_parent)
                self._active_dialog = box
                box.setWindowTitle('Authorize MCP server')
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setText(f"Server: {request['connection_id']}\n\nOpen your browser to authorize access?\n"
                    f'{endpoint.scheme}://{endpoint.netloc}{endpoint.path}\n\nCredentials are entered in the browser.')
                box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                box.setDefaultButton(QMessageBox.StandardButton.No)
                timer = QTimer(box)
                timer.setInterval(100)
                timer.timeout.connect(lambda: box.reject() if future.done() or self.shutting_down else None)
                timer.start()
                if box.exec() == QMessageBox.StandardButton.Yes and not future.done():
                    accepted = QDesktopServices.openUrl(QUrl(request['url']))
            except ValueError:
                accepted = False
            finally:
                if timer is not None:
                    timer.stop()
                if box is not None:
                    box.deleteLater()
                self._active_dialog = None
        if not future.done():
            future.set_result(accepted)

    @Slot(object)
    def register(self, state):
        register_actions(self.manager, state.connection_id)

    def watch(self, future, callback):
        def finished(current):
            try:
                result, error = current.result(), ''
            except Exception:
                result, error = None, 'MCP operation failed. Check the connection, authentication and configuration.'
            self.completed.emit((callback, result, error))
        future.add_done_callback(finished)

    def approve(self, descriptor, arguments, policy):
        if self.approval_parent is None or self.shutting_down:
            return False
        request = {'descriptor': descriptor, 'arguments': arguments, 'policy': policy,
            'done': Event(), 'expired': Event(), 'accepted': False}
        if QThread.currentThread() == self.thread():
            self._approval(request)
        else:
            self.approval_requested.emit(request)
            if not request['done'].wait(120):
                request['expired'].set()
                return False
        return request['accepted'] and not request['expired'].is_set()

    @Slot(object)
    def _approval(self, request):
        box = timer = None
        try:
            if self.approval_parent is None or self.shutting_down or request['expired'].is_set() or self._active_dialog is not None:
                return
            descriptor = request['descriptor']
            box = QMessageBox(self.approval_parent)
            self._active_dialog = box
            box.setWindowTitle('Approve MCP tool once')
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f'Server: {descriptor.connection_id}\nTool: {descriptor.name}\n'
                f"Risk: {request['policy']['risk']}\n\nSend these arguments and run this tool once?")
            box.setDetailedText(json.dumps(request['arguments'], ensure_ascii=False, indent=2))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            timer = QTimer(box)
            timer.setInterval(100)
            timer.timeout.connect(lambda: box.reject() if self.shutting_down or request['expired'].is_set() else None)
            timer.start()
            request['accepted'] = box.exec() == QMessageBox.StandardButton.Yes and not request['expired'].is_set()
        finally:
            if timer is not None:
                timer.stop()
            if box is not None:
                box.deleteLater()
                self._active_dialog = None
            request['done'].set()

    def begin_shutdown(self):
        self.shutting_down = True
        self._shutdown_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='MCP-shutdown')
        future = self._shutdown_pool.submit(self.manager.close)
        future.add_done_callback(lambda _: self._shutdown_pool.shutdown(wait=False))
        return future
