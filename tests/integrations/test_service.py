import tempfile
import unittest
import json
from pathlib import Path

import httpx

from src.integrations.credentials import CredentialService
from src.integrations.models import IntegrationConnection
from src.integrations.registry import IntegrationRegistry
from src.integrations.service import IntegrationService
from src.integrations.storage import ConnectionStorage
from src.skills.base import SkillResult


class MemorySecrets:
    def __init__(self):
        self.values = {}
    def get(self, target):
        return self.values.get(target)
    def set(self, target, value):
        self.values[target] = value
    def delete(self, target):
        self.values.pop(target, None)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.storage = ConnectionStorage(Path(self.folder.name) / 'integrations.json')
        self.secrets = CredentialService(MemorySecrets())

    def service(self, handler):
        return IntegrationService(IntegrationRegistry(), self.storage, self.secrets,
                                  transport=httpx.MockTransport(handler))

    def test_todoist_connect_and_disconnect_keep_secrets_out_of_json(self):
        service = self.service(lambda request: httpx.Response(200, json={'results': []}))
        connected = service.connect_todoist('synthetic-token')
        self.assertTrue(connected.success)
        self.assertEqual(self.storage.get('todoist').status, 'connected')
        self.assertNotIn('synthetic-token', self.storage.path.read_text(encoding='utf-8'))
        self.assertEqual(self.secrets.get('todoist'), 'synthetic-token')
        service.disconnect('todoist')
        self.assertIsNone(self.secrets.get('todoist'))
        self.assertEqual(self.storage.get('todoist').status, 'disconnected')

    def test_bad_todoist_token_marks_reauthorization(self):
        service = self.service(lambda request: httpx.Response(401))
        result = service.connect_todoist('bad-token')
        self.assertFalse(result.success)
        self.assertIsNone(self.secrets.get('todoist'))
        self.assertEqual(self.storage.get('todoist').status, 'needs_reconnect')

    def test_expired_connected_token_is_reported_without_leaking_it(self):
        responses = iter((httpx.Response(200, json={'results': []}), httpx.Response(401)))
        service = self.service(lambda request: next(responses))
        service.connect_todoist('synthetic-token')
        result = service.test_connection('todoist')
        self.assertFalse(result.success)
        self.assertEqual(self.storage.get('todoist').status, 'needs_reconnect')
        self.assertNotIn('synthetic-token', result.message)

    def test_google_connection_check_uses_saved_desktop_client_id(self):
        self.secrets.set('google_client_id', 'demo.apps.googleusercontent.com')
        self.secrets.set('google', json.dumps({'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 9999999999, 'scopes': [
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.calendarlist.readonly']}))
        self.storage.save(IntegrationConnection('google', 'connected', 'Google account',
            granted_capabilities=('google_calendar',)))
        service = self.service(lambda request: httpx.Response(200, json={'items': []}))
        self.assertTrue(service.test_connection('google').success)

    def test_cloud_action_disconnected_and_todoist_write(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'id': 'task-1'})
        service = self.service(handler)
        result = SkillResult('assignment', 'Assignment', .9, {'title': 'Homework'}, [])
        config = {'title': 'Do {title}'}
        blocked = service.execute_action('todoist_create_task', result, config)
        self.assertFalse(blocked.success)
        self.assertIn('not connected', blocked.message)
        self.assertEqual(requests, [])
        self.secrets.set('todoist', 'synthetic-token')
        self.storage.save(IntegrationConnection('todoist', 'connected', 'Todoist account',
            granted_capabilities=('todoist_tasks',)))
        created = service.execute_action('todoist_create_task', result, config)
        self.assertTrue(created.success)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, '/api/v1/tasks')

    def test_cloud_action_expired_and_uncertain_write_are_safe(self):
        self.secrets.set('todoist', 'synthetic-token')
        self.storage.save(IntegrationConnection('todoist', 'connected', 'Todoist account',
            granted_capabilities=('todoist_tasks',)))
        result = SkillResult('assignment', 'Assignment', .9, {'title': 'Homework'}, [])
        unauthorized = self.service(lambda request: httpx.Response(401))
        failed = unauthorized.execute_action('todoist_create_task', result, {'title': '{title}'})
        self.assertFalse(failed.success)
        self.assertEqual(self.storage.get('todoist').status, 'needs_reconnect')
        self.assertNotIn('synthetic-token', failed.message)
        self.storage.save(IntegrationConnection('todoist', 'connected', 'Todoist account',
            granted_capabilities=('todoist_tasks',)))
        def timeout(request):
            raise httpx.ReadTimeout('synthetic timeout')
        uncertain = self.service(timeout).execute_action('todoist_create_task', result, {'title': '{title}'})
        self.assertFalse(uncertain.success)
        self.assertIn('unknown', uncertain.message.lower())

    def test_google_calendar_and_sheets_cloud_writes_use_fixed_requests(self):
        self.secrets.set('google_client_id', 'demo.apps.googleusercontent.com')
        self.secrets.set('google', json.dumps({'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 9999999999, 'scopes': [
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
                'https://www.googleapis.com/auth/spreadsheets']}))
        self.storage.save(IntegrationConnection('google', 'connected', 'Google account',
            granted_capabilities=('google_calendar', 'google_sheets')))
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json={'values': [['Title']]})
            return httpx.Response(200, json={'id': 'created'})
        service = self.service(handler)
        result = SkillResult('assignment', 'Assignment', .9,
            {'title': 'Homework', 'due_date': '2026-09-30'}, [])
        calendar = service.execute_action('google_calendar_create_event', result,
            {'calendar_id': 'primary', 'title': '{title}', 'date_field': 'due_date'})
        sheet = service.execute_action('google_sheets_append_row', result,
            {'spreadsheet_id': 'spreadsheet123456', 'tab': 'Assignments',
             'columns': [{'column': 'Title', 'field': 'title'}]})
        self.assertTrue(calendar.success)
        self.assertTrue(sheet.success)
        self.assertEqual([request.method for request in requests], ['POST', 'GET', 'POST'])
        self.assertEqual(requests[0].url.host, 'www.googleapis.com')
        self.assertEqual(requests[2].url.host, 'sheets.googleapis.com')

    def test_resource_lists_return_names_with_stable_ids(self):
        self.secrets.set('todoist', 'synthetic-token')
        self.storage.save(IntegrationConnection('todoist', 'connected', 'Todoist account',
            granted_capabilities=('todoist_tasks',)))
        todoist = self.service(lambda request: httpx.Response(200, json={'results': [
            {'id': 'project-123', 'name': 'School'}]}))
        self.assertEqual(todoist.list_resources('todoist_create_task', {}), [('School', 'project-123')])
        self.secrets.set('google_client_id', 'demo.apps.googleusercontent.com')
        self.secrets.set('google', json.dumps({'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 9999999999, 'scopes': [
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
                'https://www.googleapis.com/auth/spreadsheets']}))
        self.storage.save(IntegrationConnection('google', 'connected', 'Google account',
            granted_capabilities=('google_calendar', 'google_sheets')))
        def handler(request):
            if '/calendar/' in request.url.path:
                return httpx.Response(200, json={'items': [{'id': 'cal-123', 'summary': 'School'}]})
            return httpx.Response(200, json={'sheets': [{'properties': {'sheetId': 5, 'title': 'Applications'}}]})
        google = self.service(handler)
        self.assertEqual(google.list_resources('google_calendar_create_event', {}), [('School', 'cal-123')])
        self.assertEqual(google.list_resources('google_sheets_append_row',
            {'spreadsheet_id': 'spreadsheet123456'}), [('Applications', 'Applications')])
