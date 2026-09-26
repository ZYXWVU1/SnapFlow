"""Known services and capabilities; no user-supplied endpoints."""
from .models import IntegrationDefinition


_DEFINITIONS = (
    IntegrationDefinition('google', 'Google',
        'Create Calendar events and append rows to Google Sheets.', 'oauth',
        ('google_calendar', 'google_sheets')),
    IntegrationDefinition('todoist', 'Todoist',
        'Create tasks from Visual Workflow results.', 'api_token',
        ('todoist_tasks',)),
)


class IntegrationRegistry:
    def __init__(self, definitions=_DEFINITIONS):
        self._definitions = {item.id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError('Duplicate integration identifier.')
        self._capabilities = {capability: item for item in definitions for capability in item.capabilities}
        if sum(map(len, (item.capabilities for item in definitions))) != len(self._capabilities):
            raise ValueError('Duplicate integration capability.')

    def definitions(self):
        return tuple(self._definitions.values())

    def get(self, integration_id):
        return self._definitions[integration_id]

    def for_capability(self, capability):
        return self._capabilities.get(capability)
