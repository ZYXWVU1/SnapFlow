"""Closed schemas: free-form strings are never accepted as operational metadata."""
from dataclasses import replace
from datetime import datetime, timezone
import math
import re
from types import MappingProxyType
from collections.abc import Mapping

from .models import AppEvent


# Keys alone cannot establish safety: values must belong to closed vocabularies,
# or be bounded booleans/numbers. Names, model IDs, paths, URLs and text are absent.
_BOOL = 'boolean'
_COUNT = 'count'
_NUMBER = 'number'
_AGENT_COUNT = 'agent_step_count'
_RUNTIME = ('local', 'cloud')
_FEATURES = ('capture', 'skills', 'workflows', 'memory', 'context', 'extensions',
             'settings', 'evaluation', 'local_models', 'diagnostics')
_ERRORS = ('provider', 'network', 'authentication', 'rate_limit', 'database',
           'migration', 'workflow', 'integration', 'file_permission', 'configuration',
           'credentials', 'unexpected', 'screenshot_capture', 'hotkey', 'update')
_SCHEMAS = {
    'app_started': ('application', {'private_mode': _BOOL}),
    'app_ready': ('application', {}),
    'app_stopped': ('application', {}),
    'recording_disabled': ('application', {}),
    'previous_session_unclean': ('application', {}),
    'capture_completed': ('capture', {}),
    'ai_request_completed': ('ai_runtime', {'runtime': _RUNTIME, 'fallback': _BOOL}),
    'model_load_completed': ('ai_runtime', {}),
    'skill_completed': ('skill', {}),
    'workflow_completed': ('workflow', {}),
    'memory_search_completed': ('memory', {'search_type': ('fts', 'semantic'), 'result_count': _COUNT}),
    'context_completed': ('context', {'source_count': _COUNT}),
    'desktop_capture': ('context', {'success': _BOOL, 'source_count': _COUNT}),
    'desktop_suggestion': ('context', {'outcome': ('shown', 'accepted', 'dismissed', 'expired', 'duplicate_suppressed')}),
    'mcp_connection_completed': ('mcp', {'transport': ('stdio', 'streamable_http')}),
    'mcp_tool_completed': ('mcp', {'risk': ('read_only', 'local_write', 'external_write', 'sensitive', 'unknown')}),
    'integration_completed': ('integration', {'integration': ('google', 'todoist')}),
    'operation_completed': ('performance', {'operation': ('app_startup', 'capture_to_result',
        'skill_classification', 'vision_inference', 'memory_search', 'context_retrieval',
        'mcp_tool_call', 'workflow_execution', 'model_load', 'startup_config', 'startup_database',
        'startup_ui', 'startup_tray', 'startup_extensions', 'ai_selection', 'ai_health',
        'response_validation', 'capture_prepare', 'result_render', 'semantic_search', 'ai_request', 'context_answer',
        'embedding', 'mcp_connect', 'mcp_discovery', 'mcp_resource', 'mcp_prompt', 'queue_wait',
        'request_prepare', 'startup_migration', 'window_visible', 'mcp_interaction',
        'agent_context', 'agent_planning', 'agent_validation', 'agent_execution',
        'agent_verification', 'agent_replanning')}),
    'resource_sampled': ('performance', {'app_ram_mb': _NUMBER, 'cpu_percent': _NUMBER,
        'threads': _COUNT, 'workers': _COUNT, 'handles': _COUNT, 'queue_depth': _COUNT,
        'inference_queue_depth': _COUNT, 'mcp_connections': _COUNT, 'local_runtime_ram_mb': _NUMBER}),
    'onboarding_completed': ('ui', {}),
    'error_occurred': ('error', {'error_category': _ERRORS}),
    'feature_visited': ('ui', {'feature': _FEATURES}),
    'feedback_exported': ('beta', {'diagnostics_attached': _BOOL}),
    'experiment_outcome': ('beta', {'experiment': ('feedback_wording',), 'variant': ('A', 'B'),
        'outcome': ('completed', 'cancelled', 'error')}),
}
for _event in ('agent_session_started', 'agent_plan_created', 'agent_plan_approved',
               'agent_step_started', 'agent_step_completed', 'agent_step_failed',
               'agent_replanned', 'agent_session_completed'):
    _SCHEMAS[_event] = ('agent', {'step_count': _AGENT_COUNT,
        'risk': ('read_only', 'local_write', 'external_write', 'sensitive', 'destructive', 'unknown')})
_COMPONENTS = frozenset(('application', 'capture', 'ai_router', 'local_runtime', 'skill',
                       'workflow', 'memory', 'context', 'mcp', 'integration', 'ui', 'beta', 'agent'))
_ID = re.compile(r'[a-f0-9]{32}')


def _identifier(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def sanitize_event(event):
    """Reject invalid envelopes; strip unregistered/unsafe properties centrally."""
    if not isinstance(event, AppEvent) or event.privacy_class != 'safe_metadata':
        raise ValueError('Only safe operational events are accepted.')
    if type(event.event_type) is not str or event.event_type not in _SCHEMAS:
        raise ValueError('Unregistered operational event.')
    category, schema = _SCHEMAS[event.event_type]
    if event.category is not None and event.category != category:
        raise ValueError('Invalid operational category.')
    if event.component is not None and event.component not in _COMPONENTS:
        raise ValueError('Unregistered operational component.')
    if not _identifier(event.event_id) or (event.session_id is not None and not _identifier(event.session_id)):
        raise ValueError('Invalid operational identifier.')
    if type(event.timestamp) is not str or len(event.timestamp) > 40:
        raise ValueError('Invalid operational timestamp.')
    try:
        timestamp = datetime.fromisoformat(event.timestamp)
        if timestamp.tzinfo is None:
            raise ValueError
        timestamp = timestamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ValueError('Invalid operational timestamp.') from None
    if event.success is not None and type(event.success) is not bool:
        raise ValueError('Invalid operational result.')
    if event.duration_ms is not None and (type(event.duration_ms) not in (int, float)
            or not math.isfinite(event.duration_ms) or not 0 <= event.duration_ms <= 7 * 86400000):
        raise ValueError('Invalid operational duration.')
    if not isinstance(event.properties, Mapping) or len(event.properties) > 32:
        raise ValueError('Invalid operational properties.')
    properties = {}
    for key, rule in schema.items():
        value = event.properties.get(key)
        if rule == _BOOL:
            valid = type(value) is bool
        elif rule == _COUNT:
            valid = type(value) is int and 0 <= value <= 1000000
        elif rule == _AGENT_COUNT:
            valid = type(value) is int and 0 <= value <= 50
        elif rule == _NUMBER:
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000000
        else:
            valid = type(value) is str and value in rule
        if valid:
            properties[key] = value
    return replace(event, category=category, timestamp=timestamp,
                   properties=MappingProxyType(properties))
