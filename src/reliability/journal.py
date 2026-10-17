"""Durable side-effect states; never persist arguments, destinations or replay code."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from threading import RLock
import uuid

from src.observability.store import _check_regular_path


def now():
    return datetime.now(timezone.utc).isoformat()


class UnavailableJournal:
    """Fail closed at write boundaries when recovery storage cannot open."""
    def __getattr__(self, name):
        def unavailable(*args, **kwargs):
            raise RuntimeError('Recovery journal unavailable; write not permitted.')
        return unavailable


class ExecutionJournal:
    def __init__(self, path, *, terminal_limit=1000):
        if type(terminal_limit) is not int or not 1 <= terminal_limit <= 10000:
            raise ValueError('Invalid journal limit.')
        self.path = Path(path)
        self.terminal_limit = terminal_limit
        self.owner_id = uuid.uuid4().hex
        self._lock = RLock()
        self._initialized = False
        _check_regular_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Recovery database belongs to a newer version.')
            if db.execute('PRAGMA integrity_check(1)').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('Recovery database needs repair.')
            if version == 0:
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                    raise ValueError('Unrecognized recovery database.')
                db.execute('PRAGMA auto_vacuum=FULL')
                db.execute('''CREATE TABLE execution_journal (
                    sequence INTEGER PRIMARY KEY, invocation_id TEXT UNIQUE NOT NULL,
                    owner_id TEXT NOT NULL, component TEXT NOT NULL, status TEXT NOT NULL,
                    planned_at TEXT NOT NULL, started_at TEXT, ended_at TEXT)''')
                db.execute('''CREATE TABLE crash_records (
                    sequence INTEGER PRIMARY KEY, crash_id TEXT NOT NULL UNIQUE, timestamp TEXT NOT NULL,
                    app_version TEXT NOT NULL, component TEXT NOT NULL, exception_type TEXT NOT NULL,
                    safe_message TEXT NOT NULL, recovery_status TEXT NOT NULL)''')
                db.execute('PRAGMA user_version=1')
        self._initialized = True

    @contextmanager
    def connection(self):
        with self._lock:
            _check_regular_path(self.path)
            for suffix in ('-journal', '-wal', '-shm'):
                _check_regular_path(self.path.with_name(self.path.name + suffix))
            if self.path.exists() and self.path.stat().st_size > 16 * 1024 * 1024:
                raise sqlite3.DatabaseError('Recovery database exceeds its bounded size.')
            target = self.path.absolute().as_uri() + '?mode=rw' if self._initialized else self.path
            db = sqlite3.connect(target, uri=self._initialized, timeout=.25)
            db.row_factory = sqlite3.Row
            try:
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={16 * 1024 * 1024 // page_size}')
                db.execute('PRAGMA secure_delete=ON')
                db.execute('PRAGMA synchronous=FULL')
                with db:
                    yield db
            finally:
                db.close()

    def _trim(self, db):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db.execute("DELETE FROM execution_journal WHERE status IN ('completed','failed','reviewed') AND ended_at < ?", (cutoff,))
        db.execute("DELETE FROM execution_journal WHERE status IN ('completed','failed','reviewed') AND sequence NOT IN "
                   "(SELECT sequence FROM execution_journal WHERE status IN ('completed','failed','reviewed') "
                   "ORDER BY sequence DESC LIMIT ?)", (self.terminal_limit,))

    def plan(self, component):
        if component not in ('integration_write', 'mcp_write', 'local_write'):
            raise ValueError('Unregistered write component.')
        invocation = uuid.uuid4().hex
        with self.connection() as db:
            self._trim(db)
            count = db.execute("SELECT count(*) FROM execution_journal WHERE status NOT IN ('completed','failed','reviewed')").fetchone()[0]
            if count >= 1024:
                raise RuntimeError('Review interrupted actions before starting more writes.')
            db.execute('INSERT INTO execution_journal (invocation_id,owner_id,component,status,planned_at) '
                       "VALUES (?,?,?,'planned',?)", (invocation, self.owner_id, component, now()))
        return invocation

    def approved(self, invocation):
        """Agent review transition; legacy trusted executors may start from planned."""
        with self.connection() as db:
            cursor = db.execute("UPDATE execution_journal SET status='approved' "
                "WHERE invocation_id=? AND owner_id=? AND status='planned'", (invocation, self.owner_id))
            if cursor.rowcount != 1:
                raise ValueError('Invalid journal approval transition.')

    def claim_desktop(self, invocation):
        """Opaque action identity; UNIQUE prevents replay even across process restarts."""
        import re
        if not isinstance(invocation, str) or not re.fullmatch('[a-f0-9]{32}', invocation):
            raise ValueError('Invalid desktop invocation identity.')
        with self.connection() as db:
            self._trim(db)
            if db.execute("SELECT count(*) FROM execution_journal WHERE status NOT IN ('completed','failed','reviewed')").fetchone()[0] >= 1024:
                raise RuntimeError('Review interrupted actions before starting more writes.')
            db.execute('INSERT INTO execution_journal (invocation_id,owner_id,component,status,planned_at) '
                       "VALUES (?,?,?,'planned',?)", (invocation, self.owner_id, 'desktop_write', now()))
        return invocation

    def checkpoint_desktop(self, invocation, status):
        previous = {'action_sent': 'started', 'verification_started': 'action_sent'}.get(status)
        if previous is None:
            raise ValueError('Invalid desktop checkpoint.')
        with self.connection() as db:
            cursor = db.execute('UPDATE execution_journal SET status=? WHERE invocation_id=? AND owner_id=? '
                "AND component='desktop_write' AND status=?", (status, invocation, self.owner_id, previous))
            if cursor.rowcount != 1:
                raise ValueError('Invalid desktop journal transition.')

    def started(self, invocation):
        with self.connection() as db:
            cursor = db.execute("UPDATE execution_journal SET status='started',started_at=? "
                "WHERE invocation_id=? AND owner_id=? AND status IN ('planned','approved')", (now(), invocation, self.owner_id))
            if cursor.rowcount != 1:
                raise ValueError('Invalid journal transition.')

    def finish(self, invocation, status):
        if status not in ('completed', 'failed', 'unknown'):
            raise ValueError('Invalid invocation outcome.')
        with self.connection() as db:
            cursor = db.execute("UPDATE execution_journal SET status=?,ended_at=? "
                "WHERE invocation_id=? AND owner_id=? AND status IN ('started','action_sent','verification_started')", (status, now(), invocation, self.owner_id))
            if cursor.rowcount != 1:
                raise ValueError('Invalid journal transition.')
            self._trim(db)

    def recover_interrupted(self):
        with self.connection() as db:
            return db.execute("UPDATE execution_journal SET status='interrupted',ended_at=? "
                "WHERE status IN ('planned','approved','started','action_sent','verification_started') AND owner_id!=?", (now(), self.owner_id)).rowcount

    def records(self, limit=1000):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM execution_journal ORDER BY sequence DESC LIMIT ?',
                                                   (max(1, min(int(limit), 10000)),))]

    def pending_review(self):
        return [row for row in self.records(10000) if row['status'] in ('interrupted', 'unknown')]

    def acknowledge(self, invocation):
        with self.connection() as db:
            db.execute("UPDATE execution_journal SET status='reviewed',ended_at=? "
                       "WHERE invocation_id=? AND status IN ('interrupted','unknown')", (now(), invocation))
            self._trim(db)
