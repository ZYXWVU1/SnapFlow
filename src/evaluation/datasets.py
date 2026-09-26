"""Versioned references to verified examples with held-out set isolation."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

DATASET_TYPES = frozenset({'development', 'validation', 'locked_test'})


@dataclass(frozen=True)
class EvaluationDataset:
    id: str
    name: str
    skill_id: str
    dataset_type: str
    example_ids: list[str]
    created_at: str
    revision: str


class DatasetStorage:
    def __init__(self, feedback_storage, path=None):
        self.feedback_storage = feedback_storage
        self.path = Path(path) if path is not None else feedback_storage.path
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS evaluation_datasets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, skill_id TEXT NOT NULL,
                dataset_type TEXT NOT NULL, example_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, revision TEXT NOT NULL)''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _dataset(row):
        return EvaluationDataset(row[0], row[1], row[2], row[3], json.loads(row[4]), row[5], row[6])

    def list_datasets(self, skill_id=None):
        query = 'SELECT * FROM evaluation_datasets'
        args = ()
        if skill_id:
            query += ' WHERE skill_id = ?'
            args = (skill_id,)
        with self._db() as db:
            rows = db.execute(query + ' ORDER BY created_at, rowid', args).fetchall()
        return [self._dataset(row) for row in rows]

    def get(self, dataset_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM evaluation_datasets WHERE id = ?', (dataset_id,)).fetchone()
        return self._dataset(row) if row else None

    def create(self, name, skill_id, dataset_type, example_ids):
        if not isinstance(name, str) or not name.strip() or dataset_type not in DATASET_TYPES:
            raise ValueError('Dataset name and type are required.')
        ids = list(dict.fromkeys(example_ids))
        if not ids:
            raise ValueError('Select at least one verified example.')
        for record_id in ids:
            record = self.feedback_storage.get(record_id)
            if record is None or record.status != 'verified' or record.skill_id != skill_id:
                raise ValueError('Dataset cases must be verified examples from one Skill.')
        for other in self.list_datasets(skill_id):
            if other.dataset_type != dataset_type and set(ids) & set(other.example_ids):
                raise ValueError('An example cannot appear in different dataset types.')
        dataset = EvaluationDataset(uuid4().hex, name.strip(), skill_id, dataset_type, ids,
                                    datetime.now(timezone.utc).isoformat(), uuid4().hex)
        with self._db() as db:
            db.execute('INSERT INTO evaluation_datasets VALUES (?, ?, ?, ?, ?, ?, ?)',
                (dataset.id, dataset.name, dataset.skill_id, dataset.dataset_type,
                 json.dumps(ids), dataset.created_at, dataset.revision))
        return dataset

    def available_examples(self, dataset):
        return [record for record_id in dataset.example_ids
                if (record := self.feedback_storage.get(record_id)) is not None and record.status == 'verified']

    def delete(self, dataset_id):
        with self._db() as db:
            db.execute('DELETE FROM evaluation_datasets WHERE id = ?', (dataset_id,))
