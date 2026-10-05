"""Local, opt-in Memory records and deterministic search."""
from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

from src.database import initialize_learning_database
from src.observability.performance import measured
from src.memory.models import MemoryRecord, MemorySearchResult
from src.modes import ModeResult
from src.paths import AppPaths
from src.skills.base import SkillResult
from src.skills.registry import SKILLS


_PRIVATE_FIELD = re.compile(r'(password|passphrase|api.?key|secret|credential|access.?token)', re.I)
_TOKENS = re.compile(r'\w+', re.UNICODE)


class DuplicateMemoryError(ValueError):
    def __init__(self, existing_id):
        super().__init__('This screenshot may already be saved.')
        self.existing_id = existing_id


def _now():
    return datetime.now(timezone.utc).isoformat()


def _searchable(data):
    parts = []

    def visit(value, key=''):
        if key and _PRIVATE_FIELD.search(key):
            return
        if key:
            parts.append(key.replace('_', ' '))
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif value is not None:
            parts.append(str(value))

    visit(data)
    return ' '.join(parts)[:200000]


class MemoryStore:
    def __init__(self, paths=None):
        self.paths = paths or AppPaths()
        initialize_learning_database(self.paths.learning_database)
        self._fts = self._ensure_fts()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.paths.learning_database)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _ensure_fts(self):
        try:
            with self._connect() as db:
                db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    memory_id UNINDEXED, title, description, searchable_text, tags
                )""")
                indexed = db.execute('SELECT count(*) FROM memory_fts').fetchone()[0]
                records = db.execute('SELECT count(*) FROM memory_records').fetchone()[0]
                if indexed != records:
                    self._rebuild_fts(db)
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _index_record(db, memory_id):
        record = db.execute('SELECT * FROM memory_records WHERE id=?', (memory_id,)).fetchone()
        db.execute('DELETE FROM memory_fts WHERE memory_id=?', (memory_id,))
        if record:
            tags = ' '.join(row[0] for row in db.execute(
                'SELECT tag FROM memory_tags WHERE memory_id=? ORDER BY tag', (memory_id,)))
            db.execute('INSERT INTO memory_fts VALUES (?, ?, ?, ?, ?)',
                (memory_id, record['title'], record['description'], record['searchable_text'], tags))

    def _rebuild_fts(self, db):
        db.execute('DELETE FROM memory_fts')
        for row in list(db.execute('SELECT id FROM memory_records')):
            self._index_record(db, row['id'])

    def rebuild_index(self):
        try:
            with self._connect() as db:
                db.execute('DROP TABLE IF EXISTS memory_fts')
                db.execute('''CREATE VIRTUAL TABLE memory_fts USING fts5(
                    memory_id UNINDEXED, title, description, searchable_text, tags
                )''')
                self._rebuild_fts(db)
            self._fts = True
        except sqlite3.Error:
            self._fts = False
        return self._fts

    @staticmethod
    def _validated_source(result):
        if isinstance(result, SkillResult) and isinstance(result.data, dict):
            source_type = 'builtin_skill' if result.skill_id in SKILLS else 'custom_skill'
            return source_type, result.skill_id, result.data
        if isinstance(result, ModeResult) and result.mode == 'extract' and isinstance(result.data, dict):
            return 'extract', None, result.data
        if isinstance(result, ModeResult) and result.mode == 'manual_note' and isinstance(result.data, dict):
            note = result.data.get('note')
            if isinstance(note, str) and note.strip() and len(note) <= 50000:
                return 'manual_note', None, {'note': note.strip()}
        raise ValueError('Save a validated Skill, Extract result, or user-written note.')

    @staticmethod
    def _validated_tags(tags):
        if not isinstance(tags, (list, tuple)) or len(tags) > 20:
            raise ValueError('Use up to 20 tags.')
        cleaned = tuple(dict.fromkeys(tag.strip() for tag in tags
            if isinstance(tag, str) and tag.strip()))
        if any(len(tag) > 60 for tag in cleaned):
            raise ValueError('Tags must be at most 60 characters.')
        return cleaned

    def find_duplicate(self, screenshot):
        if not screenshot:
            return None
        digest = hashlib.sha256(screenshot).hexdigest()
        with self._connect() as db:
            row = db.execute('SELECT id FROM memory_records WHERE content_hash=? LIMIT 1',
                             (digest,)).fetchone()
        return row['id'] if row else None

    def save_result(self, result, *, title, tags=(), description='', screenshot=None,
                    save_screenshot=False, source_created_at=None, skill_version_id=None,
                    allow_duplicate=False):
        source_type, skill_id, data = self._validated_source(result)
        title = title.strip() if isinstance(title, str) else ''
        description = description.strip() if isinstance(description, str) else ''
        if not title or len(title) > 200 or len(description) > 2000:
            raise ValueError('Use a title of 1–200 characters and a description of at most 2,000.')
        tags = self._validated_tags(tags)
        data_json = json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(data_json) > 1000000:
            raise ValueError('Extracted data is too large to save.')
        if save_screenshot and (not isinstance(screenshot, bytes) or
                                not screenshot.startswith(b'\x89PNG\r\n\x1a\n')):
            raise ValueError('A PNG screenshot is required when saving the image.')
        thumbnail_bytes = None
        if save_screenshot:
            image = QImage.fromData(screenshot, 'PNG')
            if image.isNull():
                raise ValueError('The screenshot is not a valid PNG image.')
            thumbnail = image.scaled(240, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            payload = QByteArray()
            buffer = QBuffer(payload)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not thumbnail.save(buffer, 'PNG'):
                raise ValueError('Unable to create a Memory thumbnail.')
            buffer.close()
            thumbnail_bytes = bytes(payload)
        digest = hashlib.sha256(screenshot).hexdigest() if screenshot else None
        existing = self.find_duplicate(screenshot)
        if existing and not allow_duplicate:
            raise DuplicateMemoryError(existing)
        memory_id = uuid.uuid4().hex
        saved_at = _now()
        image_name = memory_id + '.png' if save_screenshot else None
        thumbnail_name = memory_id + '-thumb.png' if save_screenshot else None
        image_path = self.paths.memory_images_dir / image_name if image_name else None
        temporary_image = self.paths.memory_images_dir / (memory_id + '.tmp') if image_name else None
        thumbnail_path = self.paths.memory_images_dir / thumbnail_name if thumbnail_name else None
        temporary_thumbnail = self.paths.memory_images_dir / (memory_id + '-thumb.tmp') if thumbnail_name else None
        try:
            if image_path:
                self.paths.memory_images_dir.mkdir(parents=True, exist_ok=True)
                if self.paths.memory_images_dir.is_symlink():
                    raise ValueError('Memory image folder cannot be a symbolic link.')
                temporary_image.write_bytes(screenshot)
                os.replace(temporary_image, image_path)
                temporary_thumbnail.write_bytes(thumbnail_bytes)
                os.replace(temporary_thumbnail, thumbnail_path)
            with self._connect() as db:
                db.execute('''INSERT INTO memory_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (memory_id, title, description, source_type, skill_id, skill_version_id,
                     data_json, _searchable(data), image_name, thumbnail_name, source_created_at,
                     saved_at, saved_at, digest, 1))
                db.executemany('INSERT INTO memory_tags VALUES (?, ?)',
                               [(memory_id, tag) for tag in tags])
                if self._fts:
                    self._index_record(db, memory_id)
        except Exception:
            if temporary_image:
                temporary_image.unlink(missing_ok=True)
            if image_path:
                image_path.unlink(missing_ok=True)
            if temporary_thumbnail:
                temporary_thumbnail.unlink(missing_ok=True)
            if thumbnail_path:
                thumbnail_path.unlink(missing_ok=True)
            raise
        return self.get_record(memory_id)

    def get_record(self, memory_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM memory_records WHERE id=?', (memory_id,)).fetchone()
            if not row:
                return None
            tags = tuple(item[0] for item in db.execute(
                'SELECT tag FROM memory_tags WHERE memory_id=? ORDER BY tag', (memory_id,)))
        return MemoryRecord(row['id'], row['title'], row['description'], row['source_type'],
            row['skill_id'], row['skill_version_id'], json.loads(row['structured_data_json']),
            row['searchable_text'], row['screenshot_path'], row['thumbnail_path'], tags,
            row['source_created_at'], row['saved_at'], row['updated_at'], row['content_hash'],
            row['schema_version'])

    def image_path(self, record):
        name = record.screenshot_reference
        if not name or name != record.id + '.png':
            return None
        path = self.paths.memory_images_dir / name
        return path if path.is_file() and not path.is_symlink() else None

    def thumbnail_path(self, record):
        name = record.thumbnail_reference
        if not name or name != record.id + '-thumb.png':
            return None
        path = self.paths.memory_images_dir / name
        return path if path.is_file() and not path.is_symlink() else None

    def update_record(self, memory_id, *, title=None, description=None, tags=None, structured_data=None):
        prior = self.get_record(memory_id)
        if prior is None:
            raise ValueError('Memory record was not found.')
        title = prior.title if title is None else title.strip()
        description = prior.description if description is None else description.strip()
        tags = prior.tags if tags is None else self._validated_tags(tags)
        data = prior.structured_data if structured_data is None else structured_data
        if not isinstance(data, dict) or not title or len(title) > 200 or len(description) > 2000:
            raise ValueError('Invalid Memory changes.')
        data_json = json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(data_json) > 1000000:
            raise ValueError('Memory data is too large.')
        changed_at = _now()
        with self._connect() as db:
            db.execute('INSERT INTO memory_revisions VALUES (?, ?, ?, ?)',
                (uuid.uuid4().hex, memory_id, json.dumps(asdict(prior), ensure_ascii=False), changed_at))
            db.execute('''UPDATE memory_records SET title=?, description=?, structured_data_json=?,
                searchable_text=?, updated_at=? WHERE id=?''',
                (title, description, data_json, _searchable(data), changed_at, memory_id))
            db.execute('DELETE FROM memory_tags WHERE memory_id=?', (memory_id,))
            db.executemany('INSERT INTO memory_tags VALUES (?, ?)',
                           [(memory_id, tag) for tag in tags])
            if self._fts:
                self._index_record(db, memory_id)
        return self.get_record(memory_id)

    def delete_record(self, memory_id):
        record = self.get_record(memory_id)
        if record is None:
            return False
        with self._connect() as db:
            if self._fts:
                db.execute('DELETE FROM memory_fts WHERE memory_id=?', (memory_id,))
            db.execute('DELETE FROM memory_records WHERE id=?', (memory_id,))
        image = self.image_path(record)
        if image:
            image.unlink(missing_ok=True)
        thumbnail = self.thumbnail_path(record)
        if thumbnail:
            thumbnail.unlink(missing_ok=True)
        return True

    @measured('memory_search', 'memory', event_type='memory_search_completed')
    def search(self, query='', filters=None, limit=20, offset=0):
        filters = filters or {}
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError('Search query must be at most 500 characters.')
        if not isinstance(filters, dict) or not 1 <= limit <= 100 or offset < 0:
            raise ValueError('Invalid search options.')
        terms = _TOKENS.findall(query.casefold())[:20]
        use_fts = bool(terms) and self._fts and not any('\u4e00' <= ch <= '\u9fff' for ch in query)
        sql = '''SELECT m.id, m.title, m.description, m.searchable_text, m.saved_at,
                 m.skill_id, m.thumbnail_path'''
        params = []
        if use_fts:
            sql += ', bm25(memory_fts) AS rank FROM memory_fts JOIN memory_records m ON m.id=memory_fts.memory_id'
            clauses = ['memory_fts MATCH ?']
            params.append(' AND '.join('"' + term.replace('"', '') + '"' for term in terms))
        else:
            sql += ', NULL AS rank FROM memory_records m'
            clauses = []
            for term in terms:
                clauses.append("(m.title || ' ' || m.description || ' ' || m.searchable_text || ' ' || "
                    "COALESCE((SELECT group_concat(tag, ' ') FROM memory_tags WHERE memory_id=m.id), '')) LIKE ? ESCAPE '\\'")
                params.append('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        for key, column in (('saved_from', 'm.saved_at'), ('saved_to', 'm.saved_at'),
                            ('source_type', 'm.source_type'), ('skill_id', 'm.skill_id')):
            value = filters.get(key)
            if value:
                operator = '>=' if key == 'saved_from' else '<=' if key == 'saved_to' else '='
                clauses.append(f'{column} {operator} ?')
                params.append(value)
        if filters.get('tag'):
            clauses.append('EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id=m.id AND t.tag=?)')
            params.append(filters['tag'])
        if filters.get('has_screenshot'):
            clauses.append('m.screenshot_path IS NOT NULL')
        if filters.get('has_structured_data'):
            clauses.append("m.structured_data_json <> '{}'")
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY ' + ('rank, m.saved_at DESC' if use_fts else 'm.saved_at DESC')
        sql += ' LIMIT ? OFFSET ?'
        params.extend((limit, offset))
        try:
            with self._connect() as db:
                rows = db.execute(sql, params).fetchall()
        except sqlite3.Error:
            if use_fts:
                self._fts = False
                return self.search(query, filters, limit, offset)
            raise
        return [MemorySearchResult(row['id'], row['title'],
            (row['description'] or row['searchable_text'])[:220], row['saved_at'],
            row['skill_id'], row['thumbnail_path'],
            -row['rank'] if row['rank'] is not None else None) for row in rows]

    @measured('semantic_search', 'memory')
    def hybrid_search(self, query='', filters=None, limit=20, offset=0, *, cancelled=None):
        index = getattr(self, 'semantic_index', None)
        return index.hybrid_search(query, filters=filters, limit=limit, offset=offset,
                                   cancelled=cancelled) if index and query.strip() else self.search(query, filters, limit, offset)
