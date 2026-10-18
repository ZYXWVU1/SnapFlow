"""Public HTTPX sync adapter with cancellable, absolute request budgets.

Only network work runs on the shared loop. Synchronous SDK callers retain their
pool lease until each cancelled task has actually finished closing its response.
"""
import asyncio
import inspect
import threading
import time

import httpx


async def _close_streams(streams):
    """Finish socket cleanup even if timeout and user cancellation coincide."""
    async def close():
        for stream in streams:
            try:
                await stream.aclose()
            except Exception:
                pass
    task = asyncio.create_task(close())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


class _Loop:
    lock = threading.Lock()
    current = None

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.users = 0
        self.thread = threading.Thread(target=self._run, name='ai-http-loop', daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        finally:
            self.loop.close()

    @classmethod
    def acquire(cls):
        with cls.lock:
            if cls.current is None:
                cls.current = cls()
            cls.current.users += 1
            return cls.current

    def release(self):
        with self.lock:
            self.users -= 1
            if self.users:
                return
            type(self).current = None
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()

    def call(self, operation, deadline=None, cancelled=None, discard=None):
        # Do not cancel run_coroutine_threadsafe's Future: its done state does
        # not acknowledge completion of the asyncio task's cancellation cleanup.
        done = threading.Event()
        state = {}

        async def run():
            try:
                if state.get('abort'):
                    raise asyncio.CancelledError()
                if cancelled and cancelled():
                    raise ValueError('AI request cancelled.')
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError()
                if deadline is None:
                    state['result'] = await operation()
                else:
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        state['result'] = await operation()
            except BaseException as exc:
                state['error'] = exc

        def finished(task):
            if task.cancelled():
                state['error'] = asyncio.CancelledError()
            done.set()

        def start():
            state['task'] = self.loop.create_task(run())
            state['task'].add_done_callback(finished)

        self.loop.call_soon_threadsafe(start)
        reason = None
        callback_error = None
        while not done.wait(.01):
            if reason is None:
                try:
                    if cancelled and cancelled():
                        reason = 'cancelled'
                    elif deadline is not None and time.monotonic() >= deadline:
                        reason = 'timed out'
                except BaseException as exc:
                    callback_error, reason = exc, 'cancelled'
                if reason:
                    state['abort'] = True
                    # start() was queued first; task lookup occurs on its loop.
                    self.loop.call_soon_threadsafe(lambda: state['task'].cancel())
        if reason is None:
            try:
                if cancelled and cancelled():
                    reason = 'cancelled'
                elif deadline is not None and time.monotonic() >= deadline:
                    reason = 'timed out'
            except BaseException as exc:
                callback_error, reason = exc, 'cancelled'
        if reason and 'result' in state and discard is not None:
            self.call(lambda: discard(state['result']))
        if callback_error is not None:
            raise callback_error
        if reason == 'cancelled':
            raise ValueError('AI request cancelled.')
        error = state.get('error')
        if reason or isinstance(error, (TimeoutError, asyncio.CancelledError)):
            raise httpx.ReadTimeout('AI request timed out.') from None
        if error is not None:
            raise error
        return state.get('result')


class _Stream(httpx.SyncByteStream):
    def __init__(self, response, loop, deadline, cancelled):
        self.response = response
        self.loop = loop
        self.deadline = deadline
        self.cancelled = cancelled
        self.iterator = response.aiter_raw().__aiter__()
        self.closed = False

    def __iter__(self):
        async def read():
            try:
                return await self.iterator.__anext__()
            except BaseException:
                await _close_streams([self.response])
                raise
        try:
            while True:
                try:
                    yield self.loop.call(read, self.deadline, self.cancelled)
                except StopAsyncIteration:
                    return
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.loop.call(self.response.aclose)


class DeadlineTransport(httpx.BaseTransport):
    """Share persistent async connections while preserving the synchronous SDK.

    scope is read on the caller thread once, before crossing the event loop.
    Its absolute deadline and cancellation callback cover headers and all reads.
    """
    def __init__(self, *, scope, trust_env, max_connections):
        self.scope = scope
        self.trust_env = trust_env
        self.limits = httpx.Limits(max_connections=max_connections,
                                  max_keepalive_connections=max_connections)
        self.lock = threading.Lock()
        self.loop = None
        # TLS/environment setup is synchronous and may be slow on Windows. Keep
        # it in the SDK factory's caller, never on the shared network loop where
        # it would delay cancellation of unrelated requests.
        self.client = httpx.AsyncClient(trust_env=self.trust_env,
            follow_redirects=False, limits=self.limits)
        self.closed = False

    def handle_request(self, request):
        deadline, cancelled = self.scope()
        if cancelled and cancelled():
            raise ValueError('AI request cancelled.')
        if deadline is not None and time.monotonic() >= deadline:
            raise httpx.ReadTimeout('AI request timed out.', request=request)
        # AI requests have materialized JSON bodies. ByteStream implements both
        # public stream interfaces, unlike a synchronous SDK request iterator.
        forwarded = httpx.Request(request.method, request.url, headers=request.headers,
                                  content=request.read(), extensions=request.extensions)
        connected = []
        previous_trace = forwarded.extensions.get('trace')

        async def trace(name, info):
            # HTTPX's public trace extension exposes the newly connected public
            # network stream. HTTPcore versions whose start_tls catches only
            # Exception leak that TCP stream on BaseException cancellation.
            if name.endswith(('.connect_tcp.complete', '.connect_unix_socket.complete')):
                connected.append(info['return_value'])
            if previous_trace is not None:
                result = previous_trace(name, info)
                if inspect.isawaitable(result):
                    await result
        forwarded.extensions['trace'] = trace
        with self.lock:
            if self.closed:
                raise RuntimeError('AI HTTP transport is closed.')
            if self.loop is None:
                self.loop = _Loop.acquire()
            loop = self.loop

        async def send():
            # Inner client owns environment proxy and certificate discovery;
            # an outer custom transport otherwise bypasses proxy discovery.
            try:
                return await self.client.send(forwarded, stream=True)
            except BaseException:
                await _close_streams(connected)
                raise

        response = loop.call(send, deadline, cancelled, discard=lambda response: response.aclose())
        extensions = dict(response.extensions, snapflow_deadline_managed=True)
        return httpx.Response(response.status_code, headers=response.headers,
            stream=_Stream(response, loop, deadline, cancelled),
            extensions=extensions)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            loop = self.loop or _Loop.acquire()
        try:
            loop.call(self.client.aclose)
        finally:
            loop.release()
