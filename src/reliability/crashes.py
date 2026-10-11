"""Safe local exception records. Raw messages, locals and frame paths are excluded."""
import asyncio
import logging
import sys
import threading
import uuid
from weakref import WeakKeyDictionary

from src.app_version import APP_VERSION
from src.diagnostics import classify_error
from .journal import ExecutionJournal, now


_EXCEPTIONS = frozenset(('ValueError', 'RuntimeError', 'TimeoutError', 'OSError', 'PermissionError',
    'ConnectionError', 'TypeError', 'KeyError', 'DatabaseError', 'OperationalError', 'MemoryError', 'Exception'))
_COMPONENTS = frozenset(('application', 'worker', 'async', 'local_runtime', 'background'))
_ACTIVE_REPORTER = None


def record_background_error(error, *, component='worker'):
    if _ACTIVE_REPORTER is not None:
        return _ACTIVE_REPORTER.record(error, component=component)


class CrashReporter:
    def __init__(self, path=None, *, journal=None, enabled=True, on_error=None):
        self.journal = journal or ExecutionJournal(path)
        self.enabled = enabled
        self.on_error = on_error
        self._installed = False
        self._loops = WeakKeyDictionary()

    def record(self, error, *, component='application'):
        if component not in _COMPONENTS:
            component = 'application'
        name = type(error).__name__
        record = {'crash_id': uuid.uuid4().hex, 'timestamp': now(), 'app_version': APP_VERSION,
                  'component': component, 'exception_type': name if name in _EXCEPTIONS else 'Exception',
                  'safe_message': classify_error(error).user_message, 'recovery_status': 'recorded'}
        if self.enabled:
            try:
                with self.journal.connection() as db:
                    db.execute('INSERT INTO crash_records (crash_id,timestamp,app_version,component,exception_type,safe_message,recovery_status) '
                        'VALUES (:crash_id,:timestamp,:app_version,:component,:exception_type,:safe_message,:recovery_status)', record)
                    db.execute('DELETE FROM crash_records WHERE sequence NOT IN '
                               '(SELECT sequence FROM crash_records ORDER BY sequence DESC LIMIT 100)')
            except Exception:
                logging.getLogger(__name__).error('Safe local error record could not be persisted.')
        if self.on_error:
            try:
                self.on_error(record)
            except Exception:
                logging.getLogger(__name__).error('Safe error notification failed.')
        return record

    def recent(self):
        with self.journal.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM crash_records ORDER BY sequence DESC LIMIT 100')]

    def _delegate_main(self, record):
        self._previous_main(RuntimeError, RuntimeError(
            record['exception_type'] + ': ' + record['safe_message'] + ' Reference: ' + record['crash_id']), None)

    def _main_hook(self, kind, error, traceback):
        self._delegate_main(self.record(error))

    def _thread_hook(self, args):
        record = self.record(args.exc_value, component='worker')
        logging.getLogger(__name__).error('Unhandled worker failure recorded [%s].', record['crash_id'])

    def attach_asyncio(self, loop):
        if loop not in self._loops:
            self._loops[loop] = loop.get_exception_handler()
            loop.set_exception_handler(lambda target, context: self.record(
                context.get('exception') or RuntimeError('Async task failed.'), component='async'))

    def install(self):
        global _ACTIVE_REPORTER
        if self._installed:
            return
        self._previous_main, self._previous_thread = sys.excepthook, threading.excepthook
        sys.excepthook, threading.excepthook = self._main_hook, self._thread_hook
        self._installed = True
        _ACTIVE_REPORTER = self

    def uninstall(self):
        global _ACTIVE_REPORTER
        if _ACTIVE_REPORTER is self:
            _ACTIVE_REPORTER = None
        if self._installed:
            if sys.excepthook == self._main_hook:
                sys.excepthook = self._previous_main
            if threading.excepthook == self._thread_hook:
                threading.excepthook = self._previous_thread
            self._installed = False
        for loop, handler in tuple(self._loops.items()):
            loop.set_exception_handler(handler)
        self._loops.clear()
