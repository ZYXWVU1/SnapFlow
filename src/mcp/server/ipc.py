"""Bounded, current-user local IPC. No TCP listener or protocol secrets in argv."""
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket

REQUEST_LIMIT = 65536
RESPONSE_LIMIT = 524288


def _identity(paths):
    digest = hashlib.sha256(str(paths.data_dir.resolve()).casefold().encode()).hexdigest()[:24]
    return 'SnapFlowMCP_' + digest, 'mcp_server_ipc_' + digest


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate IPC property.')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON number.')))


def _frame(data, limit):
    body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(body) > limit:
        raise ValueError('IPC payload exceeds limit.')
    return struct.pack('!I', len(body)) + body


class LocalIPCServer(QObject):
    completed = Signal(str, object)

    def __init__(self, paths, credentials, adapter, parent=None):
        super().__init__(parent)
        self.name, self.credential_id = _identity(paths)
        self.credentials, self.adapter = credentials, adapter
        self.listener = QLocalServer(self)
        self.listener.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.listener.newConnection.connect(self._accept)
        self.completed.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        self.connections = {}
        self.pool = None
        self.token = None
        self.inflight = 0

    def start(self):
        if self.listener.isListening():
            return
        # Never remove an existing endpoint: it may belong to another running app.
        if not self.listener.listen(self.name):
            raise ConnectionError('SnapFlow local bridge is already running or unavailable.')
        try:
            self.token = secrets.token_hex(32)
            self.credentials.set(self.credential_id, self.token)
            self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='SnapFlowIPC')
        except BaseException:
            self.listener.close()
            self.token = None
            raise

    def close(self):
        self.listener.close()
        for key in list(self.connections):
            self._drop(key)
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None
        if self.token is not None:
            self.credentials.delete(self.credential_id)
            self.token = None

    def _accept(self):
        while self.listener.hasPendingConnections():
            socket = self.listener.nextPendingConnection()
            if len(self.connections) >= 4 or self.inflight >= 4 or self.token is None:
                socket.abort()
                socket.deleteLater()
                continue
            key = secrets.token_hex(12)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._drop(k))
            timer.start(5000)
            socket.setReadBufferSize(REQUEST_LIMIT + 4)
            self.connections[key] = {'socket': socket, 'buffer': bytearray(), 'authenticated': False,
                'running': False, 'timer': timer, 'cancel': threading.Event()}
            socket.readyRead.connect(lambda k=key: self._read(k))
            socket.disconnected.connect(lambda k=key: self._drop(k))
            self._read(key)

    def _drop(self, key):
        state = self.connections.pop(key, None)
        if state:
            state['cancel'].set()
            state['timer'].stop()
            state['timer'].deleteLater()
            state['socket'].abort()
            state['socket'].deleteLater()

    def _send(self, state, result):
        state['socket'].write(_frame(result, RESPONSE_LIMIT))
        state['socket'].flush()

    def _read(self, key):
        state = self.connections.get(key)
        if state is None:
            return
        try:
            state['buffer'].extend(bytes(state['socket'].readAll()))
            if len(state['buffer']) > REQUEST_LIMIT + 4:
                raise ValueError('Request too large.')
            while len(state['buffer']) >= 4:
                length = struct.unpack('!I', state['buffer'][:4])[0]
                if length == 0 or length > REQUEST_LIMIT:
                    raise ValueError('Request too large.')
                if len(state['buffer']) < length + 4:
                    return
                data = _decode(bytes(state['buffer'][4:length + 4]))
                del state['buffer'][:length + 4]
                if not isinstance(data, dict):
                    raise ValueError('Invalid request.')
                if not state['authenticated']:
                    valid = set(data) == {'version', 'token'} and type(data['version']) is int and data['version'] == 1
                    supplied = data.get('token')
                    valid = valid and isinstance(supplied, str) and len(supplied) == 64
                    valid = valid and hmac.compare_digest(supplied.encode('utf-8'), self.token.encode('ascii'))
                    if not valid:
                        self._send(state, {'ok': False, 'error': 'permission'})
                        state['socket'].disconnectFromServer()
                        return
                    state['authenticated'] = True
                    state['timer'].start(120000)
                    self._send(state, {'ok': True})
                else:
                    if state['running'] or set(data) != {'operation', 'arguments'}:
                        raise ValueError('Invalid request.')
                    if not isinstance(data['operation'], str) or len(data['operation']) > 64 or not isinstance(data['arguments'], dict):
                        raise ValueError('Invalid request.')
                    if self.inflight >= 4:
                        raise ValueError('Server request limit reached.')
                    state['running'] = True
                    self.inflight += 1
                    future = self.pool.submit(self._execute, data, state['cancel'])
                    future.add_done_callback(lambda f, k=key: self._notify(k, f))
        except (ValueError, TypeError, RecursionError, UnicodeError, RuntimeError):
            self._drop(key)

    def _execute(self, data, cancel):
        if cancel.is_set():
            raise PermissionError('Request cancelled.')
        # An adapter can expose cancellable execution without trusting a host flag.
        if hasattr(self.adapter, 'call_cancellable'):
            result = self.adapter.call_cancellable(data['operation'], data['arguments'], cancel)
        else:
            result = self.adapter(data['operation'], data['arguments'])
        if cancel.is_set():
            raise PermissionError('Request cancelled.')
        return result

    def _complete(self, key, future):
        self.inflight = max(0, self.inflight - 1)
        state = self.connections.get(key)
        if state is None:
            return
        try:
            try:
                result = {'ok': True, 'result': future.result()}
            except PermissionError:
                result = {'ok': False, 'error': 'permission'}
            except Exception:
                result = {'ok': False, 'error': 'unavailable'}
            self._send(state, result)
            state['socket'].disconnectFromServer()
        except (ValueError, TypeError, RuntimeError):
            self._drop(key)

    def _notify(self, key, future):
        try:
            self.completed.emit(key, future)
        except RuntimeError:
            pass  # The application has already destroyed its Qt objects.


def call_ipc(paths, credentials, operation, arguments, *, token=None, timeout=120, cancel=None):
    name, credential_id = _identity(paths)
    secret = token if token is not None else credentials.get(credential_id)
    if not secret:
        raise PermissionError('Enable the MCP server in the running SnapFlow app.')
    socket = QLocalSocket()
    deadline = time.monotonic() + min(timeout, 120)
    buffer = bytearray()
    def receive():
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise PermissionError('Request cancelled.')
            buffer.extend(bytes(socket.readAll()))
            if len(buffer) >= 4:
                length = struct.unpack('!I', buffer[:4])[0]
                if not 0 < length <= RESPONSE_LIMIT:
                    raise ConnectionError('Invalid local bridge response.')
                if len(buffer) >= length + 4:
                    result = _decode(bytes(buffer[4:length + 4]))
                    del buffer[:length + 4]
                    if not isinstance(result, dict) or result.get('ok') is not True:
                        if isinstance(result, dict) and result.get('error') == 'permission':
                            raise PermissionError('SnapFlow sharing or approval is unavailable.')
                        raise ConnectionError('SnapFlow service unavailable.')
                    return result
            # PySide's blocking socket waits can retain the GIL. Short waits and
            # an explicit yield keep the app responsive even for an in-process host.
            if not socket.waitForReadyRead(5):
                if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                    break
            time.sleep(.001)
        raise ConnectionError('SnapFlow bridge timed out or disconnected.')
    try:
        socket.setReadBufferSize(RESPONSE_LIMIT + 4)
        socket.connectToServer(name)
        if not socket.waitForConnected(2000):
            raise ConnectionError('Start SnapFlow and enable its MCP server.')
        socket.write(_frame({'version': 1, 'token': secret}, REQUEST_LIMIT))
        socket.flush()
        receive()
        socket.write(_frame({'operation': operation, 'arguments': arguments}, REQUEST_LIMIT))
        socket.flush()
        return receive().get('result')
    finally:
        socket.abort()
