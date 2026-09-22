"""Local RFC 5545 calendar generation; no filesystem or network side effects."""
from datetime import date, datetime, timedelta, timezone
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(line):
    lines, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            lines.append(current)
            current = ' '
        current += char
    return '\r\n'.join([*lines, current])


def calendar_datetime(day, clock, zone):
    value = datetime.fromisoformat(day + 'T' + clock)
    if not zone:
        return value.strftime('%Y%m%dT%H%M%S')
    if zone.upper() in ('UTC', 'GMT', 'Z'):
        tz = timezone.utc
    elif match := re.fullmatch(r'(?:UTC|GMT)?([+-])(\d{2}):(\d{2})', zone.upper()):
        hours, minutes = int(match[2]), int(match[3])
        if hours > 23 or minutes > 59:
            raise ValueError('Timezone offset is invalid.')
        tz = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[1] == '+' else -1)))
    else:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Timezone is ambiguous or unavailable. Calendar export needs UTC, an explicit offset, or an IANA timezone.') from None
        # Never guess which occurrence of a DST transition the screenshot means.
        first, second = value.replace(tzinfo=tz, fold=0), value.replace(tzinfo=tz, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError('Time falls in a daylight-saving transition. An explicit UTC offset is required.')
    return value.replace(tzinfo=tz).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def create_ics(result):
    data = result.data
    assignment = result.skill_id == 'assignment'
    day = data.get('due_date' if assignment else 'date')
    if not day:
        raise ValueError('A valid date is required to create a calendar file.')
    start = data.get('due_time' if assignment else 'start_time')
    title = data.get('title') or result.title
    if assignment:
        title = ' — '.join(filter(None, (data.get('course'), title))) + ' Due'
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//SnapFlow//Smart Skills//EN',
             'CALSCALE:GREGORIAN', 'BEGIN:VEVENT', f'UID:{uuid4()}@snapflow.local',
             'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'SUMMARY:' + escape(title)]
    if start:
        lines.append('DTSTART:' + calendar_datetime(day, start, data.get('timezone')))
        if not assignment and data.get('end_time'):
            if data['end_time'] <= start:
                raise ValueError('End time must be after start time.')
            lines.append('DTEND:' + calendar_datetime(day, data['end_time'], data.get('timezone')))
    else:
        value = date.fromisoformat(day)
        lines.extend(['DTSTART;VALUE=DATE:' + value.strftime('%Y%m%d'),
                      'DTEND;VALUE=DATE:' + (value + timedelta(days=1)).strftime('%Y%m%d')])
    description = data.get('instructions_summary' if assignment else 'description') or ''
    if data.get('meeting_url'):
        description += '\n' + data['meeting_url']
    if description.strip():
        lines.append('DESCRIPTION:' + escape(description.strip()))
    if data.get('location'):
        lines.append('LOCATION:' + escape(data['location']))
    lines.extend(['END:VEVENT', 'END:VCALENDAR'])
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'
