"""Connection operations; invoke from a worker, never the Qt main thread."""
from dataclasses import dataclass
from datetime import datetime, timezone

from .credentials import CredentialError
from .google.auth import GoogleOAuth, OAuthFailure
from .google.calendar import GoogleCalendarProvider
from .google.sheets import GoogleSheetsProvider
from .http import ApiFailure, FixedApiClient
from .models import IntegrationConnection
from .todoist.client import TodoistProvider
from .actions import EXTERNAL_ACTIONS, prepare, spreadsheet_id
from src.skill_actions import ActionResult


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ConnectionOutcome:
    success: bool
    status: str
    message: str


class IntegrationService:
    def __init__(self, registry, storage, credentials, *, transport=None, google_client_id=None,
                 google_client_secret=None, browser_open=None):
        self.registry, self.storage, self.credentials = registry, storage, credentials
        self.transport = transport
        self.google_client_id, self.google_client_secret = google_client_id, google_client_secret
        self.browser_open = browser_open

    def _save(self, integration_id, status, account_label=None, capabilities=()):
        previous = self.storage.get(integration_id)
        self.storage.save(IntegrationConnection(integration_id, status,
            account_label if account_label is not None else previous.account_label if previous else None,
            previous.connected_at if previous and previous.connected_at else _now() if status == 'connected' else None,
            _now(), tuple(capabilities)))

    def _todoist(self):
        api = FixedApiClient('https://api.todoist.com/api/v1/',
                             lambda: self.credentials.get('todoist'), transport=self.transport)
        return TodoistProvider(api)

    def _google_auth(self, client_id=None):
        kwargs = {'client_secret': self.google_client_secret, 'transport': self.transport}
        if self.browser_open is not None:
            kwargs['browser_open'] = self.browser_open
        return GoogleOAuth(client_id or self.google_client_id or self.credentials.get('google_client_id'),
                           self.credentials, **kwargs)

    def _calendar(self, auth):
        api = FixedApiClient('https://www.googleapis.com/calendar/v3/',
            lambda: auth.access_token('google_calendar'), transport=self.transport)
        return GoogleCalendarProvider(api)

    def _sheets(self, auth):
        api = FixedApiClient('https://sheets.googleapis.com/v4/',
            lambda: auth.access_token('google_sheets'), transport=self.transport)
        return GoogleSheetsProvider(api)

    def list_resources(self, action_id, config):
        capability = EXTERNAL_ACTIONS.get(action_id)
        definition = self.registry.for_capability(capability) if capability else None
        if definition is None:
            raise ValueError('Unknown cloud Action.')
        connection = self.storage.get(definition.id)
        if connection is None or connection.status != 'connected' or capability not in connection.granted_capabilities:
            raise ValueError(definition.name + ' is not connected for this Action.')
        try:
            if action_id == 'google_calendar_create_event':
                resources = self._calendar(self._google_auth()).list_calendars()
                return [(item['summary'], item['id']) for item in resources
                    if isinstance(item, dict) and isinstance(item.get('summary'), str) and
                    isinstance(item.get('id'), str)]
            if action_id == 'google_sheets_append_row':
                sheet_id = spreadsheet_id(config.get('spreadsheet_id'))
                resources = self._sheets(self._google_auth()).list_sheets(sheet_id)
                return [(item['title'], item['title']) for item in resources
                    if isinstance(item, dict) and isinstance(item.get('title'), str)]
            resources = self._todoist().list_projects()
            return [(item['name'], str(item['id'])) for item in resources
                if isinstance(item, dict) and isinstance(item.get('name'), str) and
                isinstance(item.get('id'), (str, int))]
        except (ApiFailure, OAuthFailure) as exc:
            if isinstance(exc, OAuthFailure) or exc.kind == 'needs_reconnect':
                self._save(definition.id, 'needs_reconnect')
                raise ValueError(definition.name + ' needs reauthorization.') from None
            raise ValueError(str(exc)) from None
        except CredentialError:
            raise ValueError('Unable to read integration credentials.') from None

    def execute_action(self, action_id, result, config):
        capability = EXTERNAL_ACTIONS.get(action_id)
        if capability is None:
            return ActionResult(False, 'Unknown cloud Action.')
        definition = self.registry.for_capability(capability)
        if definition is None:
            return ActionResult(False, 'Integration is unavailable.')
        connection = self.storage.get(definition.id)
        if connection is None or connection.status != 'connected' or capability not in connection.granted_capabilities:
            return ActionResult(False, definition.name + ' is not connected for this Action. Connect it in Integrations.')
        try:
            data = prepare(action_id, result, config)
            if action_id == 'google_calendar_create_event':
                created = self._calendar(self._google_auth()).create_event(**data)
                message = 'Google Calendar event created.'
            elif action_id == 'google_sheets_append_row':
                created = self._sheets(self._google_auth()).append_row(**data)
                message = 'Row added to Google Sheets.'
            else:
                created = self._todoist().create_task(**data)
                message = 'Todoist task created.'
            if not isinstance(created, dict):
                return ActionResult(False, 'The service returned an unreadable result. Review the account before retrying.')
            return ActionResult(True, message, kind='cloud')
        except (ApiFailure, OAuthFailure) as exc:
            if isinstance(exc, OAuthFailure) or exc.kind == 'needs_reconnect':
                self._save(definition.id, 'needs_reconnect')
                return ActionResult(False, definition.name + ' needs reauthorization. Reconnect in Integrations.')
            if isinstance(exc, ApiFailure) and exc.kind == 'unknown_result':
                return ActionResult(False, 'The external write result is unknown. Review the account before retrying.')
            return ActionResult(False, str(exc))
        except (CredentialError, ValueError, OSError) as exc:
            return ActionResult(False, str(exc) if isinstance(exc, ValueError) else
                'The integration could not complete this Action.')

    def connect_todoist(self, token):
        self.registry.get('todoist')
        try:
            self.credentials.set('todoist', token)
            self._todoist().test_connection()
            self._save('todoist', 'connected', 'Todoist account', ('todoist_tasks',))
            return ConnectionOutcome(True, 'connected', 'Todoist connected.')
        except (ApiFailure, CredentialError, ValueError, OSError) as exc:
            self.credentials.delete('todoist')
            status = 'needs_reconnect' if isinstance(exc, ApiFailure) and exc.kind == 'needs_reconnect' else 'error'
            self._save('todoist', status)
            return ConnectionOutcome(False, status, 'Todoist connection could not be verified.')

    def connect_google(self, client_id, capabilities):
        self.registry.get('google')
        try:
            auth = self._google_auth(client_id)
            auth.connect(capabilities)
            if 'google_calendar' in capabilities:
                self._calendar(auth).test_connection()
            self.credentials.set('google_client_id', client_id)
            self.google_client_id = client_id
            self._save('google', 'connected', 'Google account', capabilities)
            return ConnectionOutcome(True, 'connected', 'Google connected.')
        except (OAuthFailure, ApiFailure, CredentialError, ValueError, OSError) as exc:
            status = 'needs_reconnect' if isinstance(exc, (OAuthFailure, ApiFailure)) else 'error'
            self._save('google', status)
            return ConnectionOutcome(False, status, 'Google connection could not be verified.')

    def test_connection(self, integration_id):
        self.registry.get(integration_id)
        try:
            if integration_id == 'todoist':
                self._todoist().test_connection()
                capabilities = ('todoist_tasks',)
            else:
                auth = self._google_auth()
                current = self.storage.get('google')
                capabilities = current.granted_capabilities if current else ()
                if not capabilities:
                    raise OAuthFailure('Google is not connected.')
                for capability in capabilities:
                    auth.access_token(capability)
                if 'google_calendar' in capabilities:
                    self._calendar(auth).test_connection()
            self._save(integration_id, 'connected', capabilities=capabilities)
            return ConnectionOutcome(True, 'connected', 'Connection successful.')
        except (ApiFailure, OAuthFailure, CredentialError, ValueError, OSError) as exc:
            status = ('needs_reconnect' if isinstance(exc, OAuthFailure) or
                      isinstance(exc, ApiFailure) and exc.kind == 'needs_reconnect' else 'error')
            self._save(integration_id, status)
            return ConnectionOutcome(False, status, 'Connection needs attention. Reconnect and try again.')

    def disconnect(self, integration_id):
        self.registry.get(integration_id)
        self.credentials.delete(integration_id)
        if integration_id == 'google':
            self.credentials.delete('google_client_id')
        self.storage.save(IntegrationConnection(integration_id, 'disconnected'))
        return ConnectionOutcome(True, 'disconnected', 'Integration disconnected.')
