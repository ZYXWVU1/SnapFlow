"""Persistent aggregate reports and case details."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class EvaluationReport:
    id: str
    skill_id: str
    skill_version_id: str
    dataset_id: str
    dataset_revision: str
    model_config_id: str
    prompt_hash: str
    executed_at: str
    test_case_count: int
    metrics: dict
    failures: list[str]


class EvaluationStorage:
    def __init__(self, feedback_storage, path=None):
        self.feedback_storage = feedback_storage
        self.path = Path(path) if path is not None else feedback_storage.path
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS evaluation_runs (
                id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, skill_version_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL, dataset_revision TEXT NOT NULL,
                model_config_id TEXT NOT NULL, prompt_hash TEXT NOT NULL,
                executed_at TEXT NOT NULL, test_case_count INTEGER NOT NULL,
                metrics TEXT NOT NULL, failures TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS evaluation_case_results (
                run_id TEXT NOT NULL, example_id TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY (run_id, example_id))''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _report(row):
        return EvaluationReport(*row[:9], json.loads(row[9]), json.loads(row[10]))

    def save(self, report, cases):
        with self._db() as db:
            db.execute('INSERT INTO evaluation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (report.id, report.skill_id, report.skill_version_id, report.dataset_id,
                 report.dataset_revision, report.model_config_id, report.prompt_hash,
                 report.executed_at, report.test_case_count,
                 json.dumps(report.metrics, allow_nan=False), json.dumps(report.failures)))
            for case in cases:
                if self.feedback_storage.get(case['example_id']) is None:
                    continue
                db.execute('INSERT INTO evaluation_case_results VALUES (?, ?, ?)',
                    (report.id, case['example_id'], json.dumps(case, ensure_ascii=False, allow_nan=False)))

    def list_reports(self, skill_id=None):
        query = 'SELECT * FROM evaluation_runs'
        args = ()
        if skill_id:
            query += ' WHERE skill_id = ?'
            args = (skill_id,)
        with self._db() as db:
            rows = db.execute(query + ' ORDER BY executed_at DESC, rowid DESC', args).fetchall()
        return [self._report(row) for row in rows]

    def get(self, report_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM evaluation_runs WHERE id = ?', (report_id,)).fetchone()
        return self._report(row) if row else None

    def case_results(self, report_id):
        with self._db() as db:
            rows = db.execute('SELECT example_id, result FROM evaluation_case_results WHERE run_id = ?',
                              (report_id,)).fetchall()
        return [json.loads(payload) for record_id, payload in rows
                if self.feedback_storage.get(record_id) is not None]
