"""Measured model dimensions with conservative revision and hardware eligibility."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

from src.database import initialize_learning_database


def _utc_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Performance timestamp is invalid.') from exc
    if parsed.tzinfo is None:
        raise ValueError('Performance timestamp must include a timezone.')
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ModelPerformanceProfile:
    runtime_id: str
    model_id: str
    model_version: str
    skill_id: str
    skill_version_id: str
    dataset_id: str
    dataset_revision: str
    case_count: int
    local: bool
    field_accuracy: float | None
    schema_validity: float | None
    failure_rate: float | None
    avg_latency_ms: float | None
    estimated_cost_per_request: float | None
    hardware_profile_id: str | None
    created_at: str
    report_id: str
    required_field_coverage: float | None = None
    missing_field_accuracy: float | None = None
    hallucination_rate: float | None = None
    classification_accuracy: float | None = None
    profile_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self):
        for name in ('runtime_id', 'model_id', 'model_version', 'skill_id', 'skill_version_id',
                     'dataset_id', 'dataset_revision', 'report_id', 'profile_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError('Performance identity is incomplete or invalid.')
        if type(self.case_count) is not int or self.case_count < 0 or type(self.local) is not bool:
            raise ValueError('Performance sample count or runtime locality is invalid.')
        if self.hardware_profile_id is not None and (not isinstance(self.hardware_profile_id, str) or
                not self.hardware_profile_id.strip() or len(self.hardware_profile_id) > 512):
            raise ValueError('Hardware profile identity is invalid.')
        for name in ('field_accuracy', 'schema_validity', 'failure_rate', 'required_field_coverage',
                     'missing_field_accuracy', 'hallucination_rate', 'classification_accuracy',
                     'avg_latency_ms', 'estimated_cost_per_request'):
            value = getattr(self, name)
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or
                    value < 0 or (name not in ('avg_latency_ms', 'estimated_cost_per_request') and value > 1)):
                raise ValueError('Performance metrics must be finite, valid measured values.')
        object.__setattr__(self, 'created_at', _utc_timestamp(self.created_at).isoformat())

    @classmethod
    def from_report(cls, report, *, runtime_id, model_id, model_version, local,
                    hardware_profile_id=None, estimated_cost_per_request=None):
        metrics = report.metrics
        return cls(runtime_id, model_id, model_version, report.skill_id, report.skill_version_id,
            report.dataset_id, report.dataset_revision, metrics.get('case_count', 0), local,
            metrics.get('field_accuracy'), metrics.get('schema_validity_rate'), metrics.get('failure_rate'),
            metrics.get('average_latency_ms'), 0.0 if local else estimated_cost_per_request,
            hardware_profile_id if local else None, report.executed_at, report.id,
            required_field_coverage=metrics.get('required_field_coverage'),
            missing_field_accuracy=metrics.get('missing_field_accuracy'),
            hallucination_rate=metrics.get('hallucination_rate'),
            classification_accuracy=metrics.get('classification_accuracy'))


class ModelPerformanceStore:
    def __init__(self, path, *, min_samples=5, max_age_days=30, clock=None):
        if type(min_samples) is not int or min_samples < 1:
            raise ValueError('Minimum benchmark samples must be a positive integer.')
        if type(max_age_days) not in (float, int) or not math.isfinite(max_age_days) or max_age_days <= 0:
            raise ValueError('Benchmark age must be a positive number of days.')
        self.path = Path(path)
        self.min_samples, self.max_age_days = min_samples, max_age_days
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        initialize_learning_database(self.path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ai_model_performance (
                profile_id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL, model_id TEXT NOT NULL,
                skill_id TEXT NOT NULL, dataset_revision TEXT NOT NULL, created_at TEXT NOT NULL,
                profile TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS ai_model_performance_identity
                ON ai_model_performance(runtime_id, model_id, skill_id, dataset_revision)''')

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, profile):
        if not isinstance(profile, ModelPerformanceProfile):
            raise TypeError('Expected a measured ModelPerformanceProfile.')
        with self._db() as db:
            db.execute('INSERT OR REPLACE INTO ai_model_performance VALUES (?, ?, ?, ?, ?, ?, ?)',
                (profile.profile_id, profile.runtime_id, profile.model_id, profile.skill_id,
                 profile.dataset_revision, profile.created_at, json.dumps(asdict(profile), allow_nan=False)))
        return profile

    def list_profiles(self, skill_id=None):
        query, values = 'SELECT profile FROM ai_model_performance', ()
        if skill_id is not None:
            query += ' WHERE skill_id = ?'
            values = (skill_id,)
        with self._db() as db:
            rows = db.execute(query + ' ORDER BY created_at DESC, rowid DESC', values).fetchall()
        return [ModelPerformanceProfile(**json.loads(payload)) for (payload,) in rows]

    def eligible(self, runtime_id, model_id, skill_id, dataset_revision, model_version,
                 hardware_profile_id, *, skill_version_id=None, dataset_id=None):
        if not all((runtime_id, model_id, skill_id, dataset_revision, model_version, skill_version_id)):
            return None
        current = self.clock()
        if current.tzinfo is None:
            raise ValueError('Benchmark clock must include a timezone.')
        current = current.astimezone(timezone.utc)
        for profile in self.list_profiles(skill_id):
            if (profile.runtime_id != runtime_id or profile.model_id != model_id or
                    profile.model_version != model_version or profile.dataset_revision != dataset_revision or
                    profile.skill_version_id != skill_version_id or
                    (dataset_id is not None and profile.dataset_id != dataset_id) or
                    profile.case_count < self.min_samples):
                continue
            age = current - _utc_timestamp(profile.created_at)
            if age < timedelta(0) or age > timedelta(days=self.max_age_days):
                continue
            if profile.local and (not hardware_profile_id or profile.hardware_profile_id != hardware_profile_id):
                continue
            return profile
        return None
