"""Bounded metadata SQLite, isolated from user databases and normal backups."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import stat
from threading import RLock
import uuid

from src.app_version import APP_VERSION, RELEASE_CHANNEL, parse_semantic_version
from .models import AppSession, now
from .privacy import sanitize_event


def _check_regular_path(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) &
                getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise OSError('Observability paths cannot be symbolic links or reparse points.')
    if path.exists() and not path.is_file():
        raise OSError('Observability destination must be a regular file.')


class LocalEventStore:
    SCHEMA_VERSION = 2

    def __init__(self, path, *, max_events=10000, max_sessions=100,
                 retention_days=7, max_bytes=8 * 1024 * 1024):
        for value, minimum, maximum in ((max_events, 1, 100000), (max_sessions, 1, 1000),
                (retention_days, 1, 365), (max_bytes, 128 * 1024, 64 * 1024 * 1024)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError('Invalid observability storage limit.')
        self.path = Path(path)
        self.max_events, self.max_sessions = max_events, max_sessions
        self.retention_days, self.max_bytes = retention_days, max_bytes
        self._lock = RLock()
        self._initialized = False
        _check_regular_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, self.SCHEMA_VERSION):
                raise ValueError('Unsupported observability schema; existing data preserved.')
            if db.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
                raise sqlite3.DatabaseError('Observability integrity check failed.')
            if version == 0:
                if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                    raise ValueError('Unrecognized observability database; existing data preserved.')
                db.execute('PRAGMA auto_vacuum=FULL')
                db.execute('''CREATE TABLE app_events (
                    sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL, category TEXT NOT NULL, timestamp TEXT NOT NULL,
                    session_id TEXT, component TEXT, duration_ms REAL, success INTEGER,
                    properties TEXT NOT NULL, privacy_class TEXT NOT NULL)''')
                db.execute('CREATE INDEX event_timestamp ON app_events(timestamp)')
                db.execute('''CREATE TABLE app_sessions (
                    sequence INTEGER PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                    started_at TEXT NOT NULL, ended_at TEXT, app_version TEXT NOT NULL,
                    release_channel TEXT NOT NULL, private_mode INTEGER NOT NULL,
                    previous_session_unclean INTEGER NOT NULL, clean_shutdown INTEGER NOT NULL,
                    status TEXT NOT NULL)''')
            if version < 2:
                db.execute('''CREATE TABLE daily_metrics (
                    day TEXT NOT NULL, event_type TEXT NOT NULL, dimension TEXT NOT NULL,
                    count INTEGER NOT NULL, success_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL, duration_sum_ms REAL NOT NULL,
                    PRIMARY KEY(day,event_type,dimension))''')
                db.execute(f'PRAGMA user_version={self.SCHEMA_VERSION}')
            self._cleanup(db)
        self._initialized = True

    @contextmanager
    def _connection(self):
        with self._lock:
            _check_regular_path(self.path)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                raise sqlite3.DatabaseError('Observability file exceeds its size limit; existing data preserved.')
            for suffix in ('-journal', '-wal', '-shm'):
                _check_regular_path(self.path.with_name(self.path.name + suffix))
            if self._initialized:
                # rw refuses a removed file instead of silently creating an empty DB.
                db = sqlite3.connect(self.path.absolute().as_uri() + '?mode=rw', uri=True, timeout=.05)
            else:
                db = sqlite3.connect(self.path, timeout=.05)
            try:
                # DELETE avoids a long-lived WAL sidecar. FULL auto-vacuum reclaims
                # retention deletions; max_page_count bounds the actual main file.
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                db.execute(f'PRAGMA max_page_count={self.max_bytes // page_size}')
                db.execute('PRAGMA secure_delete=ON')
                with db:
                    yield db
            finally:
                db.close()

    def _cleanup(self, db, *, reserve_event=0, reserve_session=0):
        aggregate_cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat()
        db.execute('DELETE FROM daily_metrics WHERE day < ?', (aggregate_cutoff,))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        db.execute('DELETE FROM app_events WHERE timestamp < ?', (cutoff,))
        db.execute('DELETE FROM app_events WHERE sequence <= '
                   '(SELECT sequence FROM app_events ORDER BY sequence DESC LIMIT 1 OFFSET ?)',
                   (self.max_events - reserve_event,))
        session_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db.execute("DELETE FROM app_sessions WHERE status != 'active' AND started_at < ?", (session_cutoff,))
        db.execute('DELETE FROM app_sessions WHERE sequence NOT IN '
                   '(SELECT sequence FROM app_sessions ORDER BY sequence DESC LIMIT ?)',
                   (self.max_sessions - reserve_session,))

    def append(self, event):
        event = sanitize_event(event)
        with self._connection() as db:
            self._cleanup(db, reserve_event=1)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
            if event.timestamp < cutoff:
                return
            db.execute('''INSERT INTO app_events
                (event_id,event_type,category,timestamp,session_id,component,duration_ms,
                 success,properties,privacy_class) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (event.event_id, event.event_type, event.category, event.timestamp, event.session_id,
                 event.component, event.duration_ms, event.success,
                 json.dumps(dict(event.properties), allow_nan=False), event.privacy_class))
            dimension = event.properties.get('runtime', event.properties.get('operation',
                event.properties.get('feature', '')))
            if event.event_type == 'app_started':
                dimension = 'private' if event.properties.get('private_mode') else 'standard'
            db.execute('''INSERT INTO daily_metrics VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(day,event_type,dimension) DO UPDATE SET
                count=count+1, success_count=success_count+excluded.success_count,
                failure_count=failure_count+excluded.failure_count,
                duration_sum_ms=duration_sum_ms+excluded.duration_sum_ms''',
                (event.timestamp[:10], event.event_type, dimension, 1,
                 int(event.success is True), int(event.success is False), event.duration_ms or 0))

    def begin_session(self, *, private_mode, app_version=APP_VERSION, release_channel=RELEASE_CHANNEL):
        if type(private_mode) is not bool or release_channel not in ('stable', 'beta', 'dev'):
            raise ValueError('Invalid session metadata.')
        if type(app_version) is not str or len(app_version) > 64:
            raise ValueError('Invalid application version.')
        # The version comes from compiled identity, never caller-controlled model/extension IDs.
        if app_version != APP_VERSION:
            raise ValueError('Session version must match compiled application identity.')
        parse_semantic_version(app_version)
        timestamp = now()
        with self._connection() as db:
            previous = db.execute('SELECT status FROM app_sessions ORDER BY sequence DESC LIMIT 1').fetchone()
            unclean = previous is not None and previous[0] == 'active'
            db.execute("UPDATE app_sessions SET status='unclean', ended_at=? WHERE status='active'", (timestamp,))
            self._cleanup(db, reserve_session=1)
            session = AppSession(uuid.uuid4().hex, timestamp, app_version, release_channel, private_mode, unclean)
            db.execute('''INSERT INTO app_sessions (session_id,started_at,app_version,release_channel,
                private_mode,previous_session_unclean,clean_shutdown,status) VALUES (?,?,?,?,?,?,0,'active')''',
                (session.session_id, timestamp, app_version, release_channel, private_mode, unclean))
        return session

    def finish_session(self, session_id, *, clean_shutdown=True):
        if type(clean_shutdown) is not bool:
            raise ValueError('Invalid session completion status.')
        with self._connection() as db:
            db.execute("UPDATE app_sessions SET ended_at=?, clean_shutdown=?, status=? "
                       "WHERE session_id=? AND status='active'",
                       (now(), clean_shutdown, 'clean' if clean_shutdown else 'disabled', session_id))

    def recent_events(self, limit=100):
        limit = max(1, min(int(limit), self.max_events))
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM app_events ORDER BY sequence DESC LIMIT ?', (limit,))]
        for row in rows:
            row['properties'] = json.loads(row['properties'])
            row['success'] = None if row['success'] is None else bool(row['success'])
        return rows

    def sessions(self):
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM app_sessions ORDER BY sequence DESC')]

    def clear_events(self):
        with self._connection() as db:
            db.execute('DELETE FROM app_events')

    def daily_metrics(self, days=7):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, min(180, int(days))))).date().isoformat()
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM daily_metrics WHERE day>=? ORDER BY day', (cutoff,))]

    def clear_analytics(self):
        with self._connection() as db:
            db.execute('DELETE FROM app_events')
            db.execute('DELETE FROM daily_metrics')
            db.execute("DELETE FROM app_sessions WHERE status != 'active'")

    def integrity_check(self):
        with self._connection() as db:
            return db.execute('PRAGMA quick_check(1)').fetchone()[0]
