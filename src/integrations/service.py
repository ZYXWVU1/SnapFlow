"""Connection operations; invoke from a worker, never the Qt main thread."""
from dataclasses import dataclass
from datetime import date, datetime, timezone

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
                 google_client_secret=None, browser_open=None, execution_journal=None):
        self.registry, self.storage, self.credentials = registry, storage, credentials
        self.transport = transport
        self.google_client_id, self.google_client_secret = google_client_id, google_client_secret
        self.browser_open = browser_open
        self.execution_journal = execution_journal

    def _save(self, integration_id, status, account_label=None, capabilities=()):
        previous = self.storage.get(integration_id)
        self.storage.save(IntegrationConnection(integration_id, status,
            account_label if account_label is not None else previous.account_label if previous else None,
            previous.connected_at if previous and previous.connected_at else _now() if status == 'connected' else None,
            _now(), tuple(capabilities)))

    def _todoist(self):
        api = FixedApiClient('https://api.todoist.com/api/v1/',
                             lambda: self.credentials.get('todoist'), transport=self.transport,
                             execution_journal=self.execution_journal)
        return TodoistProvider(api)

    def _google_auth(self, client_id=None):
        kwargs = {'client_secret': self.google_client_secret, 'transport': self.transport}
        if self.browser_open is not None:
            kwargs['browser_open'] = self.browser_open
        return GoogleOAuth(client_id or self.google_client_id or self.credentials.get('google_client_id'),
                           self.credentials, **kwargs)

    def _calendar(self, auth):
        api = FixedApiClient('https://www.googleapis.com/calendar/v3/',
            lambda: auth.access_token('google_calendar'), transport=self.transport,
            execution_journal=self.execution_journal)
        return GoogleCalendarProvider(api)

    def _sheets(self, auth):
        api = FixedApiClient('https://sheets.googleapis.com/v4/',
            lambda: auth.access_token('google_sheets'), transport=self.transport,
            execution_journal=self.execution_journal)
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

    def create_detected_calendar_event(self, result):
        """Create a reviewed Event Skill result in the user's primary calendar."""
        if getattr(result, 'skill_id', None) != 'event':
            return ActionResult(False, 'This result is not a detected event.')
        data = result.data
        title = data.get('title')
        if not isinstance(title, str) or not title.strip():
            return ActionResult(False, 'A detected event title is required.')
        event_date = data.get('date')
        try:
            date.fromisoformat(event_date)
        except (TypeError, ValueError):
            return ActionResult(False, 'A valid event date is required.')

        connection = self.storage.get('google')
        if connection is None or connection.status != 'connected' or \
                'google_calendar' not in connection.granted_capabilities:
            return ActionResult(False, 'Google Calendar is not connected. Connect it in Integrations.')

        start_time = data.get('start_time')
        end_time = data.get('end_time')
        zone = data.get('timezone')
        description = data.get('description') or ''
        if data.get('meeting_url'):
            description = '\n'.join(filter(None, (description, data['meeting_url'])))
        try:
            auth = self._google_auth()
            calendar = self._calendar(auth)
            if start_time and not zone:
                zone = calendar.primary_calendar().get('timeZone')
                if not isinstance(zone, str) or not zone.strip():
                    return ActionResult(False,
                        'Set a timezone for your primary Google Calendar before adding timed events.')
            created = calendar.create_event(
                calendar_id='primary', title=title, event_date=event_date,
                start_time=start_time, end_time=end_time, timezone=zone,
                description=description, location=data.get('location') or '')
            if not isinstance(created, dict):
                return ActionResult(False,
                    'Google Calendar returned an unreadable result. Check Calendar before retrying.')
            return ActionResult(True, 'Event added to Google Calendar.', kind='cloud')
        except (ApiFailure, OAuthFailure) as exc:
            if isinstance(exc, OAuthFailure) or exc.kind == 'needs_reconnect':
                self._save('google', 'needs_reconnect')
                return ActionResult(False,
                    'Google Calendar needs reauthorization. Reconnect and approve Calendar access.')
            if isinstance(exc, ApiFailure) and exc.kind == 'unknown_result':
                return ActionResult(False,
                    'Google may have added the event, but the response was lost. Check Calendar before retrying.')
            return ActionResult(False, str(exc))
        except (CredentialError, ValueError, OSError) as exc:
            return ActionResult(False, str(exc) if isinstance(exc, ValueError) else
                'Google Calendar could not complete the event.')

    def execute_action(self, action_id, result, config, *, execution_guard=None, cancel=None):
        def current():
            return (cancel is None or not cancel.is_set()) and (execution_guard is None or execution_guard())
        if not current():
            return ActionResult(False, 'Workflow request cancelled or revoked.')
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
                provider = self._calendar(self._google_auth())
                provider.api.execution_guard = current
                created = provider.create_event(**data)
                message = 'Google Calendar event created.'
            elif action_id == 'google_sheets_append_row':
                provider = self._sheets(self._google_auth())
                provider.api.execution_guard = current
                created = provider.append_row(**data)
                message = 'Row added to Google Sheets.'
            else:
                provider = self._todoist()
                provider.api.execution_guard = current
                created = provider.create_task(**data)
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

    def connect_google(self, capabilities):
        self.registry.get('google')
        try:
            client_id = self.google_client_id or self.credentials.get('google_client_id')
            if not client_id:
                self._save('google', 'error')
                return ConnectionOutcome(False, 'error',
                    'Google sign-in needs the app\'s client ID. Set SNAPFLOW_GOOGLE_CLIENT_ID in the source .env file or Windows build environment, then restart SnapFlow.')
            auth = self._google_auth(client_id)
            auth.connect(capabilities)
            if 'google_calendar' in capabilities:
                self._calendar(auth).test_connection()
            self._save('google', 'connected', 'Google account', capabilities)
            return ConnectionOutcome(True, 'connected', 'Google connected.')
        except (OAuthFailure, ApiFailure, CredentialError, ValueError, OSError) as exc:
            status = 'needs_reconnect' if isinstance(exc, (OAuthFailure, ApiFailure)) else 'error'
            self._save('google', status)
            if isinstance(exc, ApiFailure) and exc.status_code == 403:
                message = ('Google sign-in succeeded, but Calendar access was denied (HTTP 403). '
                           'Enable Calendar API in the same Google Cloud project as the Desktop client ID, '
                           'and confirm Calendar permissions were granted.')
            elif isinstance(exc, ApiFailure) and exc.status_code == 401:
                message = ('Google rejected the access token (HTTP 401). Disconnect Google, '
                           'restart SnapFlow, and reconnect.')
            elif isinstance(exc, OAuthFailure):
                detail = str(exc)
                if 'invalid_client' in detail or 'unauthorized_client' in detail:
                    message = ('Google rejected this OAuth client. Check that .env contains the '
                               'Desktop app client ID from your Google Cloud project, then restart SnapFlow.')
                elif 'invalid_grant' in detail:
                    message = ('Google could not redeem the sign-in code. Restart SnapFlow and '
                               'reconnect with the same Desktop app client ID.')
                else:
                    message = detail
            else:
                message = 'Google connection could not be verified. Check the app configuration and reconnect.'
            return ConnectionOutcome(False, status, message)

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
        self.storage.save(IntegrationConnection(integration_id, 'disconnected'))
        return ConnectionOutcome(True, 'disconnected', 'Integration disconnected.')
