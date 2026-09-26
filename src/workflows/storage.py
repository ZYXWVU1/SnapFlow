"""Atomic workflow configuration storage with recoverable damaged input."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import uuid
from src.skills.custom.storage import default_path
from .models import WorkflowDefinition


class WorkflowStorage:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path().with_name('workflows.json')
        self.warning = ''
        self._workflows = {}
        self._recover = False
        self._readonly = False
        self.revision = 0
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict):
                raise ValueError('Invalid storage object.')
            if type(raw.get('version')) is not int or raw['version'] != 1:
                self._readonly = True
                raise ValueError('Unsupported storage version.')
            if not isinstance(raw.get('workflows'), list):
                raise ValueError('Invalid workflows list.')
            for entry in raw['workflows']:
                try:
                    w = WorkflowDefinition.from_dict(entry)
                    if w.id in self._workflows:
                        raise ValueError('Duplicate workflow ID.')
                    self._workflows[w.id] = w
                except ValueError:
                    self._recover = True
            if self._recover:
                self.warning = 'Invalid workflows skipped; original configuration will be backed up on save.'
        except FileNotFoundError:
            pass
        except OSError:
            self._readonly = True
            self.warning = 'Unable to read workflows; changes are disabled.'
        except (ValueError, RecursionError):
            self._recover = True
            self.warning = ('Unsupported workflow storage version; changes are disabled.' if self._readonly else
                            'Damaged workflows file; original configuration will be backed up on save.')

    def list_workflows(self):
        return deepcopy(list(self._workflows.values()))

    def get_workflow(self, workflow_id):
        return deepcopy(self._workflows.get(workflow_id))

    def _commit(self, workflows):
        if self._readonly:
            raise ValueError(self.warning)
        payload = {'version': 1, 'workflows': [w.to_dict() for w in workflows.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='workflows_', suffix='.tmp', dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            for entry in json.loads(temporary.read_text(encoding='utf-8'))['workflows']:
                WorkflowDefinition.from_dict(entry)
            if self._recover and self.path.exists():
                self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.bak').write_bytes(self.path.read_bytes())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._workflows = deepcopy(workflows)
        self._recover = False
        self.warning = ''
        self.revision += 1

    def create_workflow(self, workflow):
        if workflow.id in self._workflows:
            raise ValueError('Workflow ID already exists.')
        self.save_workflow(workflow)

    def save_workflow(self, workflow):
        validated = WorkflowDefinition.from_dict(workflow.to_dict())
        self._commit(self._workflows | {validated.id: validated})

    def set_enabled(self, workflow_id, enabled):
        workflow = self.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError('Workflow no longer exists.')
        self.save_workflow(replace(workflow, enabled=enabled))

    def delete_workflow(self, workflow_id):
        if workflow_id not in self._workflows:
            raise ValueError('Workflow no longer exists.')
        self._commit({k: v for k, v in self._workflows.items() if k != workflow_id})
