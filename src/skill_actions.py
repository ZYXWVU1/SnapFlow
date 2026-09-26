"""Registry of local action payloads. Execution has no implicit external effects."""
from dataclasses import dataclass, field, replace
import json
import csv
import io
from typing import Callable

from src.actions import copy_formats
from src.calendar_export import create_ics
from src.modes import ModeResult


def readable(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return '\n\n'.join(filter(None, (readable(v) for v in value)))
    if isinstance(value, dict):
        return '\n'.join(f'{k.replace("_", " ").title()}: {readable(v)}' for k, v in value.items() if v is not None)
    return str(value)


def details(result):
    if result.presentation:
        return '\n'.join(f'{label}: {readable(result.data.get(key)) if result.data.get(key) is not None else "Not detected"}'
                         for key, label, required in result.presentation if required or result.data.get(key) is not None)
    return '\n\n'.join(f'{k.replace("_", " ").title()}: {readable(v)}'
                       for k, v in result.data.items() if v is not None and v != [] and v != '')


def table_format(result, label):
    mode_result = ModeResult('extract', '', {'content_type': 'table', **result.data})
    return copy_formats(mode_result)[label]


def markdown(result):
    if result.skill_id == 'table':
        return table_format(result, 'Copy Markdown')
    def safe(value):
        value = readable(value)
        for char in ('\\', '`', '*', '_', '[', ']', '<', '>', '#'):
            value = value.replace(char, '\\' + char)
        return value.replace('\r\n', '\n').replace('\n', '  \n  ')
    labels = {key: label for key, label, required in result.presentation}
    return f'### {safe(result.title)}\n\n' + '\n'.join(
        f'- **{safe(labels.get(key, key.replace("_", " ").title()))}:** {safe(value)}'
        for key, value in result.data.items() if value is not None and value != [] and value != '')


def custom_csv(result):
    def cell(value):
        text = readable(value)
        # Screenshot strings and user labels are untrusted spreadsheet cells.
        # True numeric data retains its numeric CSV representation.
        if type(value) not in (int, float) and text.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + text
        return text
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow([cell(label) for key, label, required in result.presentation])
    writer.writerow([cell(result.data.get(key)) for key, label, required in result.presentation])
    return stream.getvalue()


def workflow_csv(result):
    """Stable machine-readable columns for tracking workflows."""
    if result.skill_id == 'table':
        source = csv.reader(io.StringIO(table_format(result, 'Copy CSV')))
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerows(["'" + cell if cell.lstrip().startswith(('=', '+', '-', '@')) else cell
                          for cell in row] for row in source)
        return stream.getvalue()
    from src.skills.registry import SKILLS
    keys = ([key for key, label, required in result.presentation] if result.presentation else
            list(SKILLS[result.skill_id].schema))
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(keys)
    def cell(value):
        rendered = readable(value)
        return "'" + rendered if type(value) not in (int, float) and rendered.lstrip().startswith(('=', '+', '-', '@')) else rendered
    writer.writerow([cell(result.data.get(key)) for key in keys])
    return stream.getvalue()


def save_csv_payload(result):
    return custom_csv(result) if result.presentation else workflow_csv(result)


@dataclass(frozen=True)
class ActionDefinition:
    id: str
    label: str
    kind: str
    build: Callable
    enabled: Callable = lambda result: True
    config_schema: dict = field(default_factory=dict)
    risk_level: str = 'read_only'
    integration_id: str | None = None

    def preview(self, result, config):
        """Describe a prepared payload without invoking any effect adapter."""
        if self.integration_id:
            from src.integrations.actions import preview
            return preview(self.id, result, config)
        if self.id == 'show_notification':
            from src.workflows.effects import render_template
            payload = render_template(config['title'], result.data) + '\n' + render_template(config['message'], result.data)
        elif self.id == 'copy_csv' and result.skill_id == 'table':
            payload = workflow_csv(result)
        else:
            payload = self.build(result)
        destination = config.get('file_path')
        return f'{self.label}: {destination}\n{payload}' if destination else payload

    def validate_config(self, config):
        if self.integration_id:
            from src.integrations.actions import validate_config
            return validate_config(self.id, config)
        errors = []
        if not isinstance(config, dict):
            return ['Action configuration must be an object.']
        if set(config) - set(self.config_schema):
            errors.append('Unknown action configuration field.')
        for key, schema in self.config_schema.items():
            value = config.get(key)
            if schema.get('required') and (not isinstance(value, str) or not value.strip()):
                errors.append(f'{key} is required.')
            elif value is not None and not isinstance(value, str):
                errors.append(f'{key} must be text.')
        return errors


@dataclass(frozen=True)
class ActionResult:
    success: bool
    message: str = ''
    kind: str = ''
    payload: str = ''


def has_date(result):
    return bool(result.data.get('due_date') or result.data.get('date'))


_definitions = [
    ActionDefinition('copy_text', 'Copy Plain Text', 'copy', details),
    ActionDefinition('save_csv', 'Save CSV', 'save_csv', save_csv_payload),
    ActionDefinition('append_csv', 'Append CSV', 'append_csv', workflow_csv),
    ActionDefinition('show_notification', 'Show Notification', 'notification', lambda r: ''),
    ActionDefinition('copy_details', 'Copy Details', 'copy', details),
    ActionDefinition('copy_event_details', 'Copy Details', 'copy', details),
    ActionDefinition('copy_markdown', 'Copy Markdown', 'copy', markdown),
    ActionDefinition('copy_json', 'Copy JSON', 'copy', lambda r: json.dumps(r.data, indent=2, ensure_ascii=False, allow_nan=False)),
    ActionDefinition('copy_csv', 'Copy CSV', 'copy', lambda r: table_format(r, 'Copy CSV')),
    ActionDefinition('copy_table_text', 'Copy Table Text', 'copy', lambda r: '\n'.join('\t'.join(row) for row in [r.data['headers'], *r.data['rows']])),
    ActionDefinition('copy_error', 'Copy Error', 'copy', lambda r: '\n'.join(filter(None, [r.data.get('error_type'), r.data.get('error_message'), readable(r.data.get('evidence'))]))),
    ActionDefinition('copy_fix', 'Copy Fix', 'copy', lambda r: readable(r.data['suggested_fixes']), lambda r: bool(r.data.get('suggested_fixes'))),
    ActionDefinition('copy_diagnosis', 'Copy Diagnosis', 'copy', details),
    ActionDefinition('copy_meeting_link', 'Copy Meeting Link', 'copy', lambda r: r.data['meeting_url'], lambda r: bool(r.data.get('meeting_url'))),
    ActionDefinition('create_ics', 'Create Calendar File', 'save_ics', create_ics, has_date),
    ActionDefinition('ask_ai', 'Ask AI', 'ask', lambda r: 'Explain the extracted information in this screenshot.'),
    ActionDefinition('explain', 'Explain', 'ask', lambda r: 'Explain why this problem occurs and how the suggested fixes work.'),
]
ACTIONS = {action.id: replace(
    action,
    config_schema=({'file_path': {'type': 'string', 'required': True}}
                   if action.kind in ('save_csv', 'save_ics', 'append_csv') else
                   {'title': {'type': 'string', 'required': True}, 'message': {'type': 'string', 'required': True}}
                   if action.kind == 'notification' else {}),
    risk_level=('clipboard' if action.kind == 'copy' else
                'local_write' if action.kind in ('save_csv', 'save_ics', 'append_csv') else
                'external' if action.kind == 'ask' else 'read_only')) for action in _definitions}

for action_id, label, integration_id, schema in (
    ('google_calendar_create_event', 'Create Google Calendar Event', 'google',
     {'calendar_id': {}, 'title': {}, 'date_field': {}, 'start_time_field': {},
      'end_time_field': {}, 'timezone': {}, 'description': {}, 'location_field': {},
      'reminder_minutes': {}}),
    ('google_sheets_append_row', 'Append Google Sheet Row', 'google',
     {'spreadsheet_id': {}, 'tab': {}, 'columns': {}, 'create_header': {}}),
    ('todoist_create_task', 'Create Todoist Task', 'todoist',
     {'project_id': {}, 'title': {}, 'description': {}, 'due_date_field': {},
      'due_time_field': {}, 'timezone': {}, 'priority': {}}),
):
    ACTIONS[action_id] = ActionDefinition(action_id, label, 'cloud', lambda result: '',
        config_schema=schema, risk_level='external_write', integration_id=integration_id)


def execute_action(action_id, result):
    action = ACTIONS.get(action_id)
    if not action or action_id not in result.actions:
        return ActionResult(False, 'This action is not available for this result.')
    if not action.enabled(result):
        return ActionResult(False, 'Required information is missing for this action.')
    try:
        return ActionResult(True, kind=action.kind, payload=action.build(result))
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return ActionResult(False, str(exc))
