"""Registry of local action payloads. Execution has no implicit external effects."""
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ActionDefinition:
    id: str
    label: str
    kind: str
    build: Callable
    enabled: Callable = lambda result: True


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
    ActionDefinition('save_csv', 'Save CSV', 'save_csv', custom_csv, lambda r: bool(r.presentation)),
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
ACTIONS = {action.id: action for action in _definitions}


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
