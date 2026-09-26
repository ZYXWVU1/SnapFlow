"""Pure preparation and preview for the three registered cloud writes."""
from datetime import date, datetime, time
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.workflows.effects import render_template


EXTERNAL_ACTIONS = {
    'google_calendar_create_event': 'google_calendar',
    'google_sheets_append_row': 'google_sheets',
    'todoist_create_task': 'todoist_tasks',
}

FIELDS = {
    'google_calendar_create_event': {
        'calendar_id': str, 'title': str, 'date_field': str,
        'start_time_field': str, 'end_time_field': str, 'timezone': str,
        'description': str, 'location_field': str, 'reminder_minutes': int,
    },
    'google_sheets_append_row': {
        'spreadsheet_id': str, 'tab': str, 'columns': list, 'create_header': bool,
    },
    'todoist_create_task': {
        'project_id': str, 'title': str, 'description': str,
        'due_date_field': str, 'due_time_field': str, 'timezone': str, 'priority': int,
    },
}
REQUIRED = {
    'google_calendar_create_event': ('calendar_id', 'title', 'date_field'),
    'google_sheets_append_row': ('spreadsheet_id', 'tab', 'columns'),
    'todoist_create_task': ('title',),
}
FIELD_KEYS = {'date_field', 'start_time_field', 'end_time_field', 'location_field',
              'due_date_field', 'due_time_field'}
TEMPLATE_KEYS = {'title', 'description'}
_TEMPLATE = re.compile(r'\{([a-z][a-z0-9_]*)\}')
_SHEET_ID = re.compile(r'[A-Za-z0-9_-]{10,}\Z')


def spreadsheet_id(value):
    if not isinstance(value, str):
        raise ValueError('Enter a valid spreadsheet ID or URL.')
    if _SHEET_ID.fullmatch(value):
        return value
    parsed = urlparse(value)
    if parsed.scheme == 'https' and parsed.hostname == 'docs.google.com' and not parsed.username:
        match = re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]{10,})(?:/edit)?/?', parsed.path)
        if match:
            return match.group(1)
    raise ValueError('Enter a valid spreadsheet ID or URL.')


def validate_config(action_id, config, fields=None):
    if action_id not in EXTERNAL_ACTIONS or not isinstance(config, dict):
        return ['Invalid external Action configuration.']
    schema = FIELDS[action_id]
    errors = []
    if set(config) - set(schema):
        errors.append('Unknown Action configuration field.')
    for key in REQUIRED[action_id]:
        if config.get(key) in (None, '', []):
            errors.append(f'{key} is required.')
    for key, value in config.items():
        expected = schema.get(key)
        if expected is None or value is None:
            continue
        if type(value) is not expected:
            errors.append(f'{key} has the wrong type.')
            continue
        if isinstance(value, str) and (len(value) > 2000 or any(ord(char) < 32 and char not in '\n\t' for char in value)):
            errors.append(f'{key} is invalid.')
        if key in FIELD_KEYS and value and fields is not None and value not in fields:
            errors.append(f'Unknown Skill field: {value}.')
        if key in TEMPLATE_KEYS and value:
            remainder = _TEMPLATE.sub('', value)
            if '{' in remainder or '}' in remainder:
                errors.append('Only simple {field_name} placeholders are supported.')
            if fields is not None:
                errors.extend(f'Unknown Skill field: {field}.' for field in _TEMPLATE.findall(value)
                              if field not in fields)
    if action_id == 'google_sheets_append_row':
        try:
            spreadsheet_id(config.get('spreadsheet_id'))
        except ValueError as exc:
            errors.append(str(exc))
        columns = config.get('columns')
        if isinstance(columns, list):
            if not 1 <= len(columns) <= 100:
                errors.append('Configure 1 to 100 columns.')
            names = []
            for item in columns:
                if not isinstance(item, dict) or set(item) != {'column', 'field'} or any(
                    not isinstance(item.get(k), str) or not item[k].strip() or len(item[k]) > 200 or
                    any(ord(char) < 32 for char in item[k]) for k in ('column', 'field')
                ):
                    errors.append('Each column needs a name and Skill field.')
                    continue
                names.append(item['column'])
                if fields is not None and item['field'] not in fields:
                    errors.append(f"Unknown Skill field: {item['field']}.")
            if len(set(names)) != len(names):
                errors.append('Sheet column names must be unique.')
        tab = config.get('tab')
        if isinstance(tab, str) and (not tab.strip() or any(ord(char) < 32 for char in tab)):
            errors.append('Choose a valid sheet tab.')
    if action_id == 'todoist_create_task' and 'priority' in config and type(config['priority']) is int:
        if not 1 <= config['priority'] <= 4:
            errors.append('Priority must be 1 to 4.')
    if action_id == 'google_calendar_create_event' and 'reminder_minutes' in config and type(config['reminder_minutes']) is int:
        if not 0 <= config['reminder_minutes'] <= 40320:
            errors.append('Reminder must be 0 to 40320 minutes.')
    return errors


def _value(result, config, key):
    source = config.get(key)
    value = result.data.get(source) if source else None
    return None if value in (None, '') else str(value)


def _title(result, config):
    rendered = render_template(config['title'], result.data).strip()
    if not rendered:
        raise ValueError('Title is missing.')
    return rendered


def prepare(action_id, result, config):
    errors = validate_config(action_id, config, set(result.data))
    if errors:
        raise ValueError(' '.join(errors))
    if action_id == 'google_calendar_create_event':
        event_date = _value(result, config, 'date_field')
        try:
            date.fromisoformat(event_date)
        except (TypeError, ValueError):
            raise ValueError('A valid event date is required.') from None
        start_time = _value(result, config, 'start_time_field')
        end_time = _value(result, config, 'end_time_field')
        if end_time and not start_time:
            raise ValueError('End time requires a start time.')
        if start_time:
            try:
                zone = ZoneInfo(config['timezone'])
                start = datetime.combine(date.fromisoformat(event_date), time.fromisoformat(start_time), zone)
                if end_time and datetime.combine(date.fromisoformat(event_date), time.fromisoformat(end_time), zone) <= start:
                    raise ValueError('Event end must follow start.')
            except (KeyError, ZoneInfoNotFoundError, TypeError):
                raise ValueError('A valid timezone is required for event time.') from None
            except ValueError as exc:
                if str(exc) == 'Event end must follow start.':
                    raise
                raise ValueError('Invalid event time.') from None
        return {'calendar_id': config['calendar_id'], 'title': _title(result, config),
            'event_date': event_date, 'start_time': start_time,
            'end_time': end_time,
            'timezone': config.get('timezone'),
            'description': render_template(config.get('description', ''), result.data),
            'location': _value(result, config, 'location_field') or '',
            'reminder_minutes': config.get('reminder_minutes')}
    if action_id == 'google_sheets_append_row':
        columns = [(item['column'], result.data.get(item['field'])) for item in config['columns']]
        return {'spreadsheet_id': spreadsheet_id(config['spreadsheet_id']), 'tab': config['tab'],
                'columns': columns, 'create_header': config.get('create_header', False)}
    if action_id == 'todoist_create_task':
        due_date = _value(result, config, 'due_date_field')
        due_time = _value(result, config, 'due_time_field')
        due_datetime = None
        if due_time and not due_date:
            raise ValueError('Task due time requires a due date.')
        if due_date:
            try:
                day = date.fromisoformat(due_date)
            except ValueError:
                raise ValueError('Task due date must be YYYY-MM-DD.') from None
            if due_time:
                try:
                    zone = ZoneInfo(config['timezone'])
                    due_datetime = datetime.combine(day, time.fromisoformat(due_time), zone).isoformat()
                except (KeyError, ZoneInfoNotFoundError, ValueError):
                    raise ValueError('A valid timezone is required for task due time.') from None
                due_date = None
        return {'title': _title(result, config), 'project_id': config.get('project_id') or None,
            'description': render_template(config.get('description', ''), result.data),
            'due_date': due_date, 'due_datetime': due_datetime, 'priority': config.get('priority', 1)}
    raise ValueError('Unknown cloud Action.')


def preview(action_id, result, config):
    data = prepare(action_id, result, config)
    if action_id == 'google_calendar_create_event':
        return (f"Would create Google Calendar event\nCalendar: {data['calendar_id']}\n"
                f"Title: {data['title']}\nDate: {data['event_date']}\n"
                f"Time: {data['start_time'] or 'All day'}")
    if action_id == 'google_sheets_append_row':
        lines = '\n'.join(f'{name}: {value if value is not None else ""}' for name, value in data['columns'])
        return f"Would append one row\nSpreadsheet: {data['spreadsheet_id']}\nSheet: {data['tab']}\n{lines}"
    return (f"Would create Todoist task\nProject: {data['project_id'] or 'Inbox'}\n"
            f"Task: {data['title']}\nDue: {data['due_datetime'] or data['due_date'] or 'None'}")
