"""Local verified examples; screenshots are separate consented files."""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from src.skills.custom.storage import default_path as skills_path


def default_path():
    return skills_path().with_name('learning.sqlite3')


@dataclass(frozen=True)
class FeedbackRecord:
    id: str
    skill_id: str
    skill_version_id: str | None
    original_result: dict
    corrected_result: dict
    changed_fields: list[str]
    screenshot_reference: str | None
    status: str
    created_at: str


class FeedbackStorage:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()
        self.image_dir = self.path.parent / 'verified_images'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS verified_examples (
                id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, skill_version_id TEXT,
                original_result TEXT NOT NULL, corrected_result TEXT NOT NULL,
                changed_fields TEXT NOT NULL, screenshot_path TEXT, status TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS example_skill_idx ON verified_examples(skill_id)')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row):
        return FeedbackRecord(row[0], row[1], row[2], json.loads(row[3]), json.loads(row[4]),
                              json.loads(row[5]), row[6], row[7], row[8])

    def save_verified(self, extraction, screenshot=None):
        image_path = None
        if screenshot is not None:
            if not isinstance(screenshot, bytes) or not screenshot:
                raise ValueError('Screenshot must be non-empty image bytes.')
            self.image_dir.mkdir(parents=True, exist_ok=True)
            image_path = self.image_dir / (uuid4().hex + '.png')
            image_path.write_bytes(screenshot)
        record = FeedbackRecord(uuid4().hex, extraction.skill_id, extraction.skill_version_id,
            extraction.original_data.copy(), extraction.edited_data.copy(),
            list(extraction.changed_fields), str(image_path) if image_path else None,
            'verified', datetime.now(timezone.utc).isoformat())
        try:
            with self._connect() as db:
                db.execute('''INSERT INTO verified_examples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (record.id, record.skill_id, record.skill_version_id,
                     json.dumps(record.original_result, ensure_ascii=False, allow_nan=False),
                     json.dumps(record.corrected_result, ensure_ascii=False, allow_nan=False),
                     json.dumps(record.changed_fields), record.screenshot_reference,
                     record.status, record.created_at))
        except (sqlite3.Error, TypeError, ValueError):
            if image_path:
                image_path.unlink(missing_ok=True)
            raise
        return record

    def list_examples(self, skill_id=None):
        query = 'SELECT * FROM verified_examples'
        args = ()
        if skill_id:
            query += ' WHERE skill_id = ?'
            args = (skill_id,)
        query += ' ORDER BY created_at DESC, id DESC'
        with self._connect() as db:
            return [self._record(row) for row in db.execute(query, args)]

    def get(self, record_id):
        with self._connect() as db:
            row = db.execute('SELECT * FROM verified_examples WHERE id = ?', (record_id,)).fetchone()
        return self._record(row) if row else None

    def delete(self, record_id):
        record = self.get(record_id)
        if record is None:
            return
        with self._connect() as db:
            db.execute('DELETE FROM verified_examples WHERE id = ?', (record_id,))
            present = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'evaluation_case_results'").fetchone()
            if present:
                db.execute('DELETE FROM evaluation_case_results WHERE example_id = ?', (record_id,))
        if record.screenshot_reference:
            image = Path(record.screenshot_reference)
            if image.parent.resolve() == self.image_dir.resolve():
                image.unlink(missing_ok=True)

    def clear(self):
        for record in self.list_examples():
            self.delete(record.id)
