"""Small dedicated SQLite store; no browser grants or pending jobs are persisted."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from src.resume.models import MAX_PROFILES, identifier, validate_profile, validate_rule

SCHEMA_VERSION = 2
_SCHEMA = {
    'profiles': 'CREATE TABLE profiles (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)',
    'rules': 'CREATE TABLE rules (id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, revision INTEGER NOT NULL, payload TEXT NOT NULL)',
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class ResumeStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self._db.execute('PRAGMA trusted_schema=OFF')
            self._db.execute('PRAGMA foreign_keys=ON')
            version = self._db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise ValueError('Resume database requires a newer application version.')
            if version == 0:
                if self._db.execute('SELECT name FROM sqlite_master').fetchone():
                    raise ValueError('Unrecognized resume database schema.')
                with self._db:
                    self._db.execute('BEGIN IMMEDIATE')
                    for statement in _SCHEMA.values():
                        self._db.execute(statement)
                    self._db.execute('PRAGMA user_version=2')
            elif version == 1:
                # Validate and upgrade under the same write lock. No payload rewrite
                # means opaque identities, review flags and revisions stay intact.
                with self._db:
                    self._db.execute('BEGIN IMMEDIATE')
                    _validate_connection(self._db)
                    self._db.execute('PRAGMA user_version=2')
        except Exception:
            self._db.close()
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def list_profiles(self):
        with self._lock:
            return [{k: p[k] for k in ('id', 'revision', 'name')} for (raw,) in
                    self._db.execute('SELECT payload FROM profiles ORDER BY rowid') for p in [json.loads(raw)]]

    def get_profile(self, profile_id):
        identifier(profile_id)
        with self._lock:
            row = self._db.execute('SELECT payload FROM profiles WHERE id=?', (profile_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_profile(self, profile):
        value = validate_profile(profile)
        with self._lock, self._db:
            value['id'] = value['id'] or uuid.uuid4().hex
            old = self.get_profile(value['id'])
            if old is None:
                if value['revision'] != 0 or len(self.list_profiles()) >= MAX_PROFILES:
                    raise ValueError('Invalid new resume revision or profile limit reached.')
            elif value['revision'] != old['revision']:
                raise ValueError('The resume changed; reload before saving.')
            value['revision'] = (old['revision'] if old else 0) + 1
            self._db.execute('INSERT INTO profiles VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                             (value['id'], value['revision'], _json(value)))
            for rule in self.list_rules(value['id']):
                try:
                    validate_rule(rule, value)
                except ValueError:
                    if rule['enabled']:
                        rule['enabled'] = False
                        rule['revision'] += 1
                        self._db.execute('UPDATE rules SET revision=?,payload=? WHERE id=?',
                                         (rule['revision'], _json(rule), rule['id']))
        return deepcopy(value)

    def delete_profile(self, profile_id):
        identifier(profile_id)
        with self._lock, self._db:
            self._db.execute('DELETE FROM profiles WHERE id=?', (profile_id,))

    def list_rules(self, profile_id):
        identifier(profile_id)
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute(
                'SELECT payload FROM rules WHERE profile_id=? ORDER BY rowid', (profile_id,))]

    def save_rule(self, rule):
        with self._lock, self._db:
            profile = self.get_profile(rule.get('profile_id', ''))
            if profile is None:
                raise ValueError('Resume profile does not exist.')
            value = validate_rule(rule, profile)
            value['id'] = value['id'] or uuid.uuid4().hex
            old = self._db.execute('SELECT profile_id,revision FROM rules WHERE id=?', (value['id'],)).fetchone()
            if (old is None and value['revision'] != 0) or (old is not None and
                    (old[0] != value['profile_id'] or old[1] != value['revision'])):
                raise ValueError('The category rule changed; reload before saving.')
            if old is None and self._db.execute('SELECT count(*) FROM rules').fetchone()[0] >= 2000:
                raise ValueError('Too many category rules.')
            value['revision'] = (old[1] if old else 0) + 1
            self._db.execute('INSERT INTO rules VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                             (value['id'], value['profile_id'], value['revision'], _json(value)))
            return deepcopy(value)

    def delete_rule(self, rule_id):
        identifier(rule_id)
        with self._lock, self._db:
            self._db.execute('DELETE FROM rules WHERE id=?', (rule_id,))


def _validate_connection(db):
    """Validate a consistent database snapshot without changing any records."""
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] not in (1, SCHEMA_VERSION) or db.execute('PRAGMA integrity_check(1)').fetchone()[0] != 'ok':
            raise ValueError('Resume backup version or integrity is invalid.')
        rows = db.execute('SELECT type,name,sql FROM sqlite_master').fetchall()
        norm = lambda sql: ''.join(sql.lower().split())
        tables = {name: sql for kind, name, sql in rows if kind == 'table'}
        if set(tables) != set(_SCHEMA) or any(norm(tables[name]) != norm(sql) for name, sql in _SCHEMA.items()):
            raise ValueError('Unrecognized resume backup schema.')
        if any(kind != 'table' and not (kind == 'index' and name in ('sqlite_autoindex_profiles_1', 'sqlite_autoindex_rules_1') and sql is None)
               for kind, name, sql in rows):
            raise ValueError('Resume backup contains unsupported SQL objects.')
        if db.execute('SELECT count(*) FROM profiles').fetchone()[0] > MAX_PROFILES or db.execute('SELECT count(*) FROM rules').fetchone()[0] > 2000:
            raise ValueError('Resume backup contains too many records.')
        profiles = {}
        for pid, revision, raw in db.execute('SELECT id,revision,payload FROM profiles'):
            value = validate_profile(json.loads(raw))
            if value['id'] != pid or value['revision'] != revision or revision < 1:
                raise ValueError('Invalid resume backup identity.')
            profiles[pid] = value
        for rid, pid, revision, raw in db.execute('SELECT id,profile_id,revision,payload FROM rules'):
            if pid not in profiles:
                raise ValueError('Orphaned resume backup rule.')
            value = validate_rule(json.loads(raw), profiles[pid])
            if value['id'] != rid or value['revision'] != revision or revision < 1:
                raise ValueError('Invalid resume rule backup identity.')
    except (sqlite3.DatabaseError, json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError('Invalid resume database contents.') from exc


def validate_database(path):
    """Validate v1/v2 imported records and SQL schema without mutating the file."""
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Resume backup is oversized.')
    db = sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        _validate_connection(db)
    finally:
        db.close()


def backup_database(source, destination):
    validate_database(source)
    first = sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro', uri=True)
    second = sqlite3.connect(destination)
    try:
        first.backup(second)
    finally:
        second.close()
        first.close()
    validate_database(destination)
    return destination
