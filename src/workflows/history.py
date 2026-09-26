"""Bounded execution metadata. Extracted values and previews never reach disk."""
import json
import os
from pathlib import Path
import tempfile
from src.skills.custom.storage import default_path


class WorkflowHistory:
    def __init__(self, path=None, limit=100):
        self.path = Path(path) if path is not None else default_path().with_name('workflow_history.json')
        self.limit = limit
        self.warning = ''
        self._entries = []
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or type(raw.get('version')) is not int or raw['version'] != 1:
                raise ValueError('Unsupported history version.')
            entries = raw.get('entries')
            if not isinstance(entries, list):
                raise ValueError('Invalid history entries.')
            keys = {'execution_id', 'workflow_id', 'workflow_name', 'skill_id',
                    'started_at', 'finished_at', 'status', 'steps'}
            step_keys = {'step_id', 'action_id', 'status'}
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != keys or not isinstance(entry['steps'], list):
                    raise ValueError('Invalid history entry.')
                if any(not isinstance(entry[key], str) for key in keys - {'steps', 'finished_at'}):
                    raise ValueError('Invalid history metadata.')
                if entry['finished_at'] is not None and not isinstance(entry['finished_at'], str):
                    raise ValueError('Invalid history timestamp.')
                if any(not isinstance(step, dict) or set(step) != step_keys or
                       any(not isinstance(step[key], str) for key in step_keys) for step in entry['steps']):
                    raise ValueError('Invalid history step.')
            self._entries = entries[-limit:]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.warning = 'Workflow history could not be read. New history will not overwrite it.'

    def list_entries(self):
        return json.loads(json.dumps(self._entries))

    def record(self, workflow, execution, skill_id):
        if execution.preview:
            return
        if self.warning:
            return
        entry = {'execution_id': execution.execution_id, 'workflow_id': workflow.id,
                 'workflow_name': workflow.name, 'skill_id': skill_id,
                 'started_at': execution.started_at, 'finished_at': execution.finished_at,
                 'status': execution.status,
                 'steps': [{'step_id': step.step_id, 'action_id': step.action_id,
                            'status': step.status} for step in execution.steps]}
        entries = (self._entries + [entry])[-self.limit:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='workflow_history_', suffix='.tmp', dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'entries': entries}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._entries = entries
