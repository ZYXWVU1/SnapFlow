"""Installed desktop OAuth with PKCE, loopback callback, and OS-stored tokens."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import httpx


AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPES = {
    'google_calendar': (
        'https://www.googleapis.com/auth/calendar.events',
        'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
    ),
    'google_sheets': ('https://www.googleapis.com/auth/spreadsheets',),
}


class OAuthFailure(RuntimeError):
    pass


def validate_callback(query, expected_state):
    if query.get('state') != [expected_state] or len(query.get('code', [])) != 1 or query.get('error'):
        raise OAuthFailure('Google authorization was not completed safely.')
    return query['code'][0]


class GoogleOAuth:
    def __init__(self, client_id, credentials, *, client_secret=None, transport=None,
                 clock=time.time, browser_open=webbrowser.open):
        if not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com'):
            raise ValueError('A Google Desktop OAuth client ID is required.')
        self.client_id, self.client_secret = client_id, client_secret
        self.credentials, self.clock, self.browser_open = credentials, clock, browser_open
        self.http = httpx.Client(transport=transport, timeout=20.0, follow_redirects=False)

    @staticmethod
    def _scopes(capabilities):
        if not capabilities or any(item not in SCOPES for item in capabilities):
            raise ValueError('Choose Calendar, Sheets, or both.')
        return tuple(dict.fromkeys(scope for item in capabilities for scope in SCOPES[item]))

    def authorization_url(self, capabilities, redirect_uri, state, verifier):
        scopes = self._scopes(capabilities)
        parsed = urlparse(redirect_uri)
        try:
            port = parsed.port
        except ValueError:
            port = None
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.username or parsed.password or not port or parsed.path != '/' or
            parsed.query or parsed.fragment or not 43 <= len(verifier) <= 128):
            raise ValueError('Invalid desktop authorization request.')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        query = urlencode({
            'client_id': self.client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
            'scope': ' '.join(scopes), 'state': state, 'access_type': 'offline',
            'prompt': 'consent', 'code_challenge': challenge, 'code_challenge_method': 'S256',
        })
        return AUTH_URL + '?' + query

    def _token_request(self, data):
        try:
            response = self.http.post(TOKEN_URL, data=data)
            if response.status_code != 200:
                raise OAuthFailure('Google authorization needs to be renewed.')
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('access_token'), str):
                raise OAuthFailure('Google returned an invalid authorization response.')
            return payload
        except (httpx.RequestError, ValueError):
            raise OAuthFailure('Unable to complete Google authorization.') from None

    def exchange_code(self, code, redirect_uri, verifier, capabilities):
        scopes = self._scopes(capabilities)
        data = {'code': code, 'client_id': self.client_id, 'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code', 'code_verifier': verifier}
        if self.client_secret:
            data['client_secret'] = self.client_secret
        payload = self._token_request(data)
        existing = self._load()
        refresh = payload.get('refresh_token') or (existing or {}).get('refresh_token')
        if not refresh:
            raise OAuthFailure('Google did not provide offline access. Reconnect and allow access.')
        granted = payload.get('scope', ' '.join(scopes)).split()
        record = {'access_token': payload['access_token'], 'refresh_token': refresh,
                  'expires_at': self.clock() + int(payload.get('expires_in', 3600)),
                  'scopes': granted}
        self.credentials.set('google', json.dumps(record))
        return record

    def _load(self):
        raw = self.credentials.get('google')
        if not raw:
            return None
        try:
            record = json.loads(raw)
            if not isinstance(record, dict) or not isinstance(record.get('scopes'), list) or \
                    any(not isinstance(scope, str) for scope in record['scopes']) or \
                    not isinstance(record.get('access_token'), str) or not record['access_token'] or \
                    not isinstance(record.get('refresh_token'), str) or not record['refresh_token'] or \
                    type(record.get('expires_at')) not in (int, float) or \
                    not math.isfinite(record['expires_at']):
                raise ValueError()
            return record
        except ValueError:
            raise OAuthFailure('Stored Google authorization is unreadable. Reconnect.') from None

    def access_token(self, capability):
        required = set(self._scopes((capability,)))
        record = self._load()
        if not record:
            raise OAuthFailure('Google is not connected.')
        if not required.issubset(set(record['scopes'])):
            raise OAuthFailure('Google needs authorization for this capability.')
        if record.get('expires_at', 0) <= self.clock() + 60:
            data = {'client_id': self.client_id, 'refresh_token': record['refresh_token'],
                    'grant_type': 'refresh_token'}
            if self.client_secret:
                data['client_secret'] = self.client_secret
            payload = self._token_request(data)
            record['access_token'] = payload['access_token']
            record['refresh_token'] = payload.get('refresh_token') or record['refresh_token']
            record['expires_at'] = self.clock() + int(payload.get('expires_in', 3600))
            self.credentials.set('google', json.dumps(record))
        return record['access_token']

    def connect(self, capabilities, *, timeout=180):
        capabilities = tuple(capabilities)
        self._scopes(capabilities)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        received = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    if urlparse(self.path).path != '/':
                        raise OAuthFailure('Invalid authorization callback.')
                    received['code'] = validate_callback(parse_qs(urlparse(self.path).query), state)
                    message = b'Connection received. You can return to Visual Workflow AI.'
                    self.send_response(200)
                except OAuthFailure:
                    received['error'] = True
                    message = b'Connection was not completed. Return to Visual Workflow AI.'
                    self.send_response(400)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(message)))
                self.end_headers()
                self.wfile.write(message)

            def log_message(self, *_args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Callback)
        server.timeout = timeout
        redirect_uri = f'http://127.0.0.1:{server.server_port}/'
        try:
            url = self.authorization_url(capabilities, redirect_uri, state, verifier)
            if not self.browser_open(url):
                raise OAuthFailure('Unable to open the browser for Google authorization.')
            server.handle_request()
        finally:
            server.server_close()
        if received.get('error') or not received.get('code'):
            raise OAuthFailure('Google authorization was canceled or timed out.')
        return self.exchange_code(received['code'], redirect_uri, verifier, capabilities)

    def disconnect(self):
        self.credentials.delete('google')
