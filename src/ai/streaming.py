"""Optional bounded prose streaming shared by cloud and loopback adapters."""
import threading
import time
from types import SimpleNamespace


class StreamingOutput:
    """Request-owned delivery state; never stored in persistent request metadata."""
    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.usage = None
        self.guard = None

    def emit(self, text):
        if self.guard:
            self.guard()
        self.started = True
        self.callback(text)


def _unsupported_stream(error):
    # Only a rejected streaming option permits one complete-response retry.
    # Authentication, quota, connection and partial-response failures do not.
    if getattr(error, 'status_code', None) not in (400, 422):
        return False
    body = getattr(error, 'body', None)
    if not isinstance(body, dict):
        return False
    detail = body.get('error', body)
    if not isinstance(detail, dict):
        return False
    message = str(detail.get('message', '')).lower()
    parameter = str(detail.get('param', '')).lower()
    return (parameter in ('stream', 'stream_options') or
            ('stream' in message and any(word in message for word in
             ('unsupported', 'not supported', 'unknown', 'unrecognized'))))


def complete(client, payload, output=None, *, cancelled=None, deadline=None, guard=None):
    """Return a completion-shaped result; expose deltas only for opted-in prose.

    A watchdog closes a stalled HTTP stream on cancellation/deadline. The outer
    cancellable worker continues to own its capacity until SDK work really exits.
    """
    if output is None:
        return client.chat.completions.create(**payload)

    def check():
        if cancelled and cancelled():
            raise ValueError('AI request cancelled.')
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('AI request timed out.')
        if output.guard:
            output.guard()
        if guard:
            guard()

    check()
    try:
        response = client.chat.completions.create(**payload, stream=True,
                                                 stream_options={'include_usage': True})
    except Exception as error:
        if not output.started and _unsupported_stream(error):
            check()
            return client.chat.completions.create(**payload)
        raise

    done = threading.Event()
    def close():
        try:
            response.close()
        except Exception:
            pass
    def watch():
        while not done.wait(.02):
            try:
                check()
            except Exception:
                close()
                return
    # The application transport already cancels each async read and awaits its
    # cleanup. Avoid a second close racing that read; retain the watchdog only
    # for injected SDK-compatible synchronous streams.
    managed = getattr(getattr(response, 'response', None), 'extensions', {}).get('snapflow_deadline_managed', False)
    watcher = None if managed else threading.Thread(target=watch, name='snapflow-stream-stop', daemon=True)
    if watcher is not None:
        watcher.start()
    pieces, length, usage, finish = [], 0, None, None
    try:
        for chunk in response:
            check()
            if getattr(chunk, 'usage', None) is not None:
                usage = output.usage = chunk.usage
            choices = getattr(chunk, 'choices', None)
            if not choices:
                continue
            choice = choices[0]
            already_finished = finish is not None
            reason = getattr(choice, 'finish_reason', None)
            if reason is not None:
                finish = reason
            delta = getattr(choice, 'delta', None)
            text = getattr(delta, 'content', None) or getattr(delta, 'refusal', None)
            if not text:
                continue
            if not isinstance(text, str) or already_finished:
                raise ValueError('The AI service returned an invalid text stream.')
            length += len(text)
            if length > 2_000_000:
                raise ValueError('The AI response is too large.')
            pieces.append(text)
            output.emit(text)
        check()
        if finish is None:
            raise ValueError('The AI response stream was incomplete. Retry the request.')
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(
            message=SimpleNamespace(content=''.join(pieces), refusal=None), finish_reason=finish)])
    finally:
        done.set()
        close()
        if watcher is not None:
            watcher.join(.1)


class DeltaBuffer:
    """Bounded request-owned text; UI drains at paint cadence, never per token."""
    def __init__(self):
        self._lock = threading.Lock()
        self._pieces = []
        self._length = 0
        self._closed = False

    def append(self, text):
        with self._lock:
            if self._closed:
                return
            if self._length + len(text) > 2_000_000:
                raise ValueError('The AI response is too large.')
            self._pieces.append(text)
            self._length += len(text)

    def take(self):
        with self._lock:
            result = ''.join(self._pieces)
            self._pieces.clear()
            self._length = 0
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._pieces.clear()
            self._length = 0
