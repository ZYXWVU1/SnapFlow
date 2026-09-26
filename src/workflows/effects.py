"""Local workflow effects. Qt actions are delegated to a main-thread callback."""
import csv
import io
from pathlib import Path
import re
import threading
from src.skill_actions import ActionResult, workflow_csv

_locks_guard = threading.Lock()
_path_locks = {}
_field = re.compile(r'\{([a-z][a-z0-9_]*)\}')


def render_template(template, data):
    if not isinstance(template, str):
        raise ValueError('Template must be text.')
    if '{' in _field.sub('', template) or '}' in _field.sub('', template):
        raise ValueError('Only simple {field_name} placeholders are supported.')
    result = _field.sub(lambda match: '' if data.get(match.group(1)) is None else str(data[match.group(1)]), template)
    return result


def _lock_for(path):
    with _locks_guard:
        return _path_locks.setdefault(str(path), threading.Lock())


class WorkflowEffects:
    def __init__(self, ui_action=None, integration_service=None):
        self.ui_action = ui_action
        self.integration_service = integration_service

    def execute(self, action, result, config):
        try:
            if action.integration_id:
                if self.integration_service is None:
                    return ActionResult(False, 'Integration service is unavailable.')
                return self.integration_service.execute_action(action.id, result, config)
            if action.kind == 'notification':
                title = render_template(config['title'], result.data)
                message = render_template(config['message'], result.data)
                if self.ui_action is None:
                    return ActionResult(False, 'UI action adapter is unavailable.')
                return self.ui_action('notification', title, message)
            payload = workflow_csv(result) if action.id == 'copy_csv' and result.skill_id == 'table' else action.build(result)
            if action.kind in ('copy', 'ask'):
                if self.ui_action is None:
                    return ActionResult(False, 'UI action adapter is unavailable.')
                return self.ui_action(action.kind, payload, '')
            if action.kind in ('save_csv', 'save_ics', 'append_csv'):
                destination = Path(config['file_path']).expanduser()
                if not destination.is_absolute() or not destination.parent.is_dir():
                    return ActionResult(False, 'Choose an existing absolute file destination.')
                with _lock_for(destination):
                    if action.kind == 'append_csv':
                        records = list(csv.reader(io.StringIO(payload)))
                        if not records or len(records) < 2:
                            return ActionResult(False, 'CSV output is empty.')
                        if destination.exists():
                            with destination.open('r', encoding='utf-8-sig', newline='') as stream:
                                existing = csv.reader(stream)
                                if next(existing, None) != records[0]:
                                    return ActionResult(False, 'Existing CSV columns do not match this Workflow.')
                            with destination.open('rb') as stream:
                                stream.seek(-1, 2)
                                needs_newline = stream.read(1) not in (b'\n', b'\r')
                            with destination.open('a', encoding='utf-8', newline='') as stream:
                                if needs_newline:
                                    stream.write('\n')
                                csv.writer(stream).writerows(records[1:])
                        else:
                            with destination.open('w', encoding='utf-8', newline='') as stream:
                                csv.writer(stream).writerows(records)
                    else:
                        destination.write_bytes(payload.encode('utf-8'))
                return ActionResult(True, str(destination))
            return ActionResult(False, 'Unsupported Workflow Action.')
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return ActionResult(False, 'Unable to complete the configured Action. Check its data and destination.')
