from .base import Skill
from .validation import date_value, fields, time_value, url


class EventSkill(Skill):
    id, title = 'event', 'Event'
    schema = dict.fromkeys(('title', 'date', 'start_time', 'end_time', 'timezone', 'location',
                            'meeting_platform', 'meeting_url', 'meeting_id', 'passcode', 'organizer', 'description'), 'string or null')
    instructions = ('Use YYYY-MM-DD dates and HH:MM 24-hour times. If year is absent or ambiguous use null. '
                    'Only include timezone, URL and passcode when explicitly visible. '
                    'meeting_platform: zoom, google_meet, microsoft_teams, webex, other.')
    action_ids = ('create_ics', 'copy_meeting_link', 'copy_event_details', 'ask_ai')
    presentation = tuple((key, key.replace('_', ' ').title()) for key in schema)

    def normalize(self, raw):
        data, warnings = fields(raw, self.schema, {'date': date_value, 'start_time': time_value,
                                                  'end_time': time_value, 'meeting_url': url})
        platform = data['meeting_platform']
        if platform:
            platform = platform.lower().replace(' ', '_')
            data['meeting_platform'] = platform if platform in ('zoom', 'google_meet', 'microsoft_teams', 'webex') else 'other'
        if data['end_time'] and (not data['start_time'] or data['end_time'] <= data['start_time']):
            data['end_time'] = None
            warnings.append('End time is ambiguous or not after start time; omitted from calendar export.')
        return data, warnings

    def actions(self, data):
        return [key for key in self.action_ids if key != 'copy_meeting_link' or data['meeting_url']]
