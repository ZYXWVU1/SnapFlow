"""Version history layered beside the existing atomic custom-Skill JSON store."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from src.skills.custom.models import CustomSkillDefinition
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.skills.registry import SkillRegistry
from src.workflows.validator import validate_workflow


@dataclass(frozen=True)
class SkillVersion:
    version_id: str
    skill_id: str
    version_label: str
    definition_snapshot: dict
    created_at: str
    description: str
    status: str
    evaluation_id: str | None = None


class SkillVersionManager:
    def __init__(self, skill_storage, workflow_storage=None, path=None):
        self.skill_storage = skill_storage
        self.workflow_storage = workflow_storage
        self.path = Path(path) if path is not None else skill_storage.path.with_name('learning.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS skill_versions (
                version_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, version_label TEXT NOT NULL,
                definition_snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT NOT NULL, evaluation_id TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS skill_version_idx ON skill_versions(skill_id)')
        for definition in skill_storage.list_skills():
            self.record_current(definition)

    def record_current(self, definition):
        if not self.list_versions(definition.id):
            return self._insert(definition, 'v1.0', 'Original Skill', 'published')
        return None

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _version(row):
        return SkillVersion(row[0], row[1], row[2], json.loads(row[3]), row[4], row[5], row[6], row[7])

    def _insert(self, definition, label, description, status):
        version = SkillVersion(uuid4().hex, definition.id, label, definition.to_dict(),
            datetime.now(timezone.utc).isoformat(), description, status)
        with self._db() as db:
            db.execute('INSERT INTO skill_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (version.version_id, version.skill_id, version.version_label,
                 json.dumps(version.definition_snapshot, ensure_ascii=False, allow_nan=False),
                 version.created_at, version.description, version.status, version.evaluation_id))
        return version

    def list_versions(self, skill_id):
        with self._db() as db:
            rows = db.execute('SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY created_at, rowid',
                              (skill_id,)).fetchall()
        return [self._version(row) for row in rows]

    def get(self, version_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM skill_versions WHERE version_id = ?', (version_id,)).fetchone()
        return self._version(row) if row else None

    def create_draft(self, definition, description='Proposed Skill change'):
        definition = CustomSkillDefinition.from_dict(definition.to_dict())
        if self.skill_storage.get_skill(definition.id) is None:
            raise ValueError('Skill no longer exists.')
        labels = self.list_versions(definition.id)
        number = max((int(v.version_label.split('.')[1]) for v in labels), default=0) + 1
        return self._insert(definition, f'v1.{number}', description, 'draft')

    def compatibility_errors(self, definition):
        if self.workflow_storage is None:
            return []
        registry = SkillRegistry(self.skill_storage)
        candidate = RuntimeCustomSkill(definition)
        original_get = registry.get
        registry.get = lambda skill_id: candidate if skill_id == definition.id else original_get(skill_id)
        errors = []
        for workflow in self.workflow_storage.list_workflows():
            if workflow.trigger.skill_id == definition.id:
                errors.extend(f'{workflow.name}: {issue}' for issue in validate_workflow(workflow, registry))
        return errors

    def publish(self, version_id):
        version = self.get(version_id)
        if version is None or version.status != 'draft':
            raise ValueError('Only an unpublished draft can be published.')
        definition = CustomSkillDefinition.from_dict(version.definition_snapshot)
        issues = self.compatibility_errors(definition)
        if issues:
            raise ValueError('Dependent Workflows need review:\n' + '\n'.join(issues))
        self.skill_storage.save_skill(definition)
        with self._db() as db:
            db.execute("UPDATE skill_versions SET status = 'archived' WHERE skill_id = ? AND status = 'published'",
                       (version.skill_id,))
            db.execute("UPDATE skill_versions SET status = 'published' WHERE version_id = ?", (version_id,))
        return self.get(version_id)

    def restore(self, version_id):
        old = self.get(version_id)
        if old is None or old.status == 'draft':
            raise ValueError('Select a published historical version.')
        draft = self.create_draft(CustomSkillDefinition.from_dict(old.definition_snapshot),
                                  'Restored from ' + old.version_label)
        return self.publish(draft.version_id)

    def delete_draft(self, version_id):
        version = self.get(version_id)
        if version is None or version.status != 'draft':
            raise ValueError('Only an unpublished draft can be deleted.')
        with self._db() as db:
            db.execute('DELETE FROM skill_versions WHERE version_id = ?', (version_id,))

    def compare(self, left_id, right_id):
        left, right = self.get(left_id), self.get(right_id)
        if left is None or right is None or left.skill_id != right.skill_id:
            raise ValueError('Choose two versions of the same Skill.')
        return {key: (left.definition_snapshot.get(key), right.definition_snapshot.get(key))
                for key in set(left.definition_snapshot) | set(right.definition_snapshot)
                if left.definition_snapshot.get(key) != right.definition_snapshot.get(key)}
