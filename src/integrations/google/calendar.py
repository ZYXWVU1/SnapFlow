"""Fixed Google Calendar operations for Visual Workflow Actions."""
from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class GoogleCalendarProvider:
    def __init__(self, api):
        self.api = api

    def list_calendars(self):
        response = self.api.request('GET', 'users/me/calendarList')
        return response.get('items', [])

    def test_connection(self):
        self.list_calendars()
        return True

    def create_event(self, calendar_id, title, event_date, *, start_time=None, end_time=None,
                     timezone=None, duration_minutes=60, description='', location='',
                     reminder_minutes=None):
        if not isinstance(calendar_id, str) or not calendar_id.strip() or any(
            ord(char) < 32 for char in calendar_id
        ):
            raise ValueError('Choose a calendar.')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Event title is required.')
        try:
            day = date.fromisoformat(event_date)
        except (TypeError, ValueError):
            raise ValueError('Event date must be YYYY-MM-DD.') from None
        body = {'summary': title.strip(), 'description': description, 'location': location}
        if start_time:
            if not timezone:
                raise ValueError('A timezone is required for timed events.')
            try:
                zone = ZoneInfo(timezone)
                start = datetime.combine(day, time.fromisoformat(start_time), zone)
                if end_time:
                    end = datetime.combine(day, time.fromisoformat(end_time), zone)
                else:
                    if type(duration_minutes) is not int or not 1 <= duration_minutes <= 1440:
                        raise ValueError('Event duration must be 1–1440 minutes.')
                    end = start + timedelta(minutes=duration_minutes)
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise ValueError('Invalid event time or timezone.') from None
            if end <= start:
                raise ValueError('Event end must follow start.')
            body['start'] = {'dateTime': start.isoformat(), 'timeZone': timezone}
            body['end'] = {'dateTime': end.isoformat(), 'timeZone': timezone}
        else:
            if end_time:
                raise ValueError('End time requires a start time.')
            body['start'] = {'date': day.isoformat()}
            body['end'] = {'date': (day + timedelta(days=1)).isoformat()}
        if reminder_minutes is not None:
            if type(reminder_minutes) is not int or not 0 <= reminder_minutes <= 40320:
                raise ValueError('Invalid reminder.')
            body['reminders'] = {'useDefault': False,
                                 'overrides': [{'method': 'popup', 'minutes': reminder_minutes}]}
        path = 'calendars/' + quote(calendar_id, safe='') + '/events'
        return self.api.request('POST', path, json=body, write=True)
