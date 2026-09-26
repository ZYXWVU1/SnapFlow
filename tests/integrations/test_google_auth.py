import json
import unittest
from urllib.parse import parse_qs, urlparse

import httpx

from src.integrations.credentials import CredentialService
from src.integrations.google.auth import GoogleOAuth, OAuthFailure, validate_callback


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, target):
        return self.values.get(target)

    def set(self, target, value):
        self.values[target] = value

    def delete(self, target):
        self.values.pop(target, None)


class GoogleOAuthTests(unittest.TestCase):
    def test_authorization_url_has_state_pkce_and_selected_scopes(self):
        auth = GoogleOAuth('demo.apps.googleusercontent.com', CredentialService(MemorySecrets()))
        url = auth.authorization_url(('google_calendar',), 'http://127.0.0.1:1234/',
                                     'state-123', 'v' * 64)
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params['state'], ['state-123'])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['access_type'], ['offline'])
        self.assertIn('calendar.events', params['scope'][0])
        self.assertNotIn('spreadsheets', params['scope'][0])
        self.assertNotIn('include_granted_scopes', params)
        with self.assertRaises(ValueError):
            auth.authorization_url(('google_calendar',), 'http://127.0.0.1:invalid/',
                                   'state-123', 'v' * 64)

    def test_exchange_saves_secret_and_refresh_preserves_refresh_token(self):
        backend = MemorySecrets()
        seen = []
        def respond(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(200, json={'access_token': 'access-1',
                    'refresh_token': 'refresh-1', 'expires_in': 1,
                    'scope': 'https://www.googleapis.com/auth/calendar.events '
                             'https://www.googleapis.com/auth/calendar.calendarlist.readonly'})
            return httpx.Response(200, json={'access_token': 'access-2', 'expires_in': 3600})
        auth = GoogleOAuth('demo.apps.googleusercontent.com', CredentialService(backend),
            transport=httpx.MockTransport(respond), clock=lambda: 1000)
        auth.exchange_code('code', 'http://127.0.0.1:1234/', 'v' * 64, ('google_calendar',))
        self.assertEqual(auth.access_token('google_calendar'), 'access-2')
        stored = json.loads(next(iter(backend.values.values())))
        self.assertEqual(stored['refresh_token'], 'refresh-1')
        self.assertNotIn('refresh-1', repr(auth))
        self.assertEqual(seen[1].url.path, '/token')

    def test_callback_state_mismatch_is_rejected(self):
        with self.assertRaises(OAuthFailure):
            validate_callback({'state': ['wrong'], 'code': ['secret-code']}, 'expected')
        self.assertEqual(validate_callback({'state': ['expected'], 'code': ['code']}, 'expected'), 'code')

    def test_missing_scope_requires_reauthorization(self):
        backend = MemorySecrets()
        credentials = CredentialService(backend)
        credentials.set('google', json.dumps({'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 9999, 'scopes': ['https://www.googleapis.com/auth/calendar.events',
                                          'https://www.googleapis.com/auth/calendar.calendarlist.readonly']}))
        auth = GoogleOAuth('demo.apps.googleusercontent.com', credentials, clock=lambda: 1000)
        with self.assertRaises(OAuthFailure):
            auth.access_token('google_sheets')

    def test_malformed_stored_expiry_requires_reauthorization(self):
        credentials = CredentialService(MemorySecrets())
        credentials.set('google', json.dumps({'access_token': 'access', 'refresh_token': 'refresh',
            'expires_at': 'invalid', 'scopes': ['https://www.googleapis.com/auth/spreadsheets']}))
        auth = GoogleOAuth('demo.apps.googleusercontent.com', credentials, clock=lambda: 1000)
        with self.assertRaises(OAuthFailure):
            auth.access_token('google_sheets')
