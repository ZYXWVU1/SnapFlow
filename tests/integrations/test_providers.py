import unittest

import httpx

from src.integrations.google.calendar import GoogleCalendarProvider
from src.integrations.google.sheets import GoogleSheetsProvider
from src.integrations.todoist.client import TodoistProvider
from src.integrations.http import ApiFailure, FixedApiClient


def api(origin, handler):
    return FixedApiClient(origin, lambda: 'synthetic-token',
                          transport=httpx.MockTransport(handler))


class ProviderTests(unittest.TestCase):
    def test_calendar_all_day_event_uses_exclusive_end(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'id': 'event-1', 'htmlLink': 'https://calendar.google.com/event'})
        provider = GoogleCalendarProvider(api('https://www.googleapis.com/calendar/v3/', respond))
        created = provider.create_event('primary', 'Homework 5', '2026-09-30')
        self.assertEqual(created['id'], 'event-1')
        self.assertEqual(seen[0].url.path, '/calendar/v3/calendars/primary/events')
        self.assertEqual(__import__('json').loads(seen[0].content)['start'], {'date': '2026-09-30'})
        self.assertEqual(__import__('json').loads(seen[0].content)['end'], {'date': '2026-10-01'})

    def test_calendar_rejects_missing_date_and_unzoned_time(self):
        provider = GoogleCalendarProvider(api('https://www.googleapis.com/calendar/v3/',
            lambda request: self.fail('Invalid event must not call API')))
        with self.assertRaises(ValueError):
            provider.create_event('primary', 'Homework', '')
        with self.assertRaises(ValueError):
            provider.create_event('primary', 'Homework', '2026-09-30', start_time='23:59')

    def test_sheets_validates_headers_before_single_raw_append(self):
        seen = []
        def respond(request):
            seen.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json={'values': [['Company', 'Position']]})
            return httpx.Response(200, json={'updates': {'updatedRange': 'Applications!A2:B2'}})
        provider = GoogleSheetsProvider(api('https://sheets.googleapis.com/v4/', respond))
        result = provider.append_row('spreadsheet-1', 'Applications',
                                     [('Company', 'Microsoft'), ('Position', 'SWE Intern')])
        self.assertEqual(result['updates']['updatedRange'], 'Applications!A2:B2')
        self.assertEqual([request.method for request in seen], ['GET', 'POST'])
        self.assertEqual(seen[1].url.params['valueInputOption'], 'RAW')
        self.assertEqual(__import__('json').loads(seen[1].content)['values'], [['Microsoft', 'SWE Intern']])

    def test_sheets_refuses_mismatched_headers_without_write(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'values': [['Different']]})
        provider = GoogleSheetsProvider(api('https://sheets.googleapis.com/v4/', respond))
        with self.assertRaises(ValueError):
            provider.append_row('spreadsheet-1', 'Applications', [('Company', 'Microsoft')])
        self.assertEqual(len(seen), 1)

    def test_sheets_can_create_header_and_row_when_empty(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={} if request.method == 'GET' else {'updates': {'updatedRows': 2}})
        provider = GoogleSheetsProvider(api('https://sheets.googleapis.com/v4/', respond))
        provider.append_row('spreadsheet-1', 'Applications', [('Company', 'Microsoft')], create_header=True)
        self.assertEqual(__import__('json').loads(seen[1].content)['values'], [['Company'], ['Microsoft']])

    def test_todoist_task_has_only_configured_due_date(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={'id': 'task-1', 'url': 'https://todoist.com/task/1'})
        provider = TodoistProvider(api('https://api.todoist.com/api/v1/', respond))
        created = provider.create_task('Homework 5', project_id='project-1', priority=2)
        self.assertEqual(created['id'], 'task-1')
        payload = __import__('json').loads(seen[0].content)
        self.assertEqual(payload['content'], 'Homework 5')
        self.assertNotIn('due_date', payload)
        self.assertEqual(seen[0].url.path, '/api/v1/tasks')

    def test_todoist_timeout_after_write_is_uncertain(self):
        def timeout(request):
            raise httpx.ReadTimeout('synthetic secret')
        provider = TodoistProvider(api('https://api.todoist.com/api/v1/', timeout))
        with self.assertRaises(ApiFailure) as caught:
            provider.create_task('Apply for internship')
        self.assertEqual(caught.exception.kind, 'unknown_result')
