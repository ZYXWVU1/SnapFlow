"""User-authored feedback and exact, closed-schema diagnostics exports. No upload."""
from collections import Counter
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from src.app_version import APP_VERSION
from src.paths import AppPaths
from .flags import FLAGS, require_plain_path

CATEGORIES = ('Bug', 'Feature Request', 'Confusing UX', 'Performance Problem', 'Other')
EVENT_TYPES = frozenset(('app_started', 'app_ready', 'app_stopped', 'recording_disabled',
    'previous_session_unclean', 'capture_completed', 'ai_request_completed', 'model_load_completed',
    'skill_completed', 'workflow_completed', 'memory_search_completed', 'context_completed',
    'mcp_connection_completed', 'mcp_tool_completed', 'integration_completed', 'operation_completed',
    'resource_sampled', 'error_occurred', 'feature_visited', 'feedback_exported'))
COMPONENTS = frozenset(('application', 'capture', 'ai_router', 'local_runtime', 'skill',
    'workflow', 'memory', 'context', 'mcp', 'integration', 'ui', 'beta', 'worker', 'async', 'background'))
ERROR_CATEGORIES = frozenset(('provider', 'network', 'authentication', 'rate_limit', 'database',
    'migration', 'workflow', 'integration', 'file_permission', 'configuration', 'credentials',
    'unexpected', 'screenshot_capture', 'hotkey', 'update'))
_ID = re.compile(r'[a-f0-9]{32}')
_RESOURCES = frozenset(('app_ram_mb', 'cpu_percent', 'threads', 'workers', 'handles', 'queue_depth',
                        'inference_queue_depth', 'mcp_connections', 'local_runtime_ram_mb'))


def _number(value, maximum=1000000000):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def _json_bytes(payload):
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


@dataclass(frozen=True)
class FeedbackReport:
    category: str
    title: str
    description: str
    feedback_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError('Choose a feedback category.')
        for value, limit, label in ((self.title, 160, 'title'), (self.description, 20000, 'description')):
            if type(value) is not str or not value.strip() or len(value) > limit or '\x00' in value:
                raise ValueError(f'Enter a {label} within {limit} characters.')
        if type(self.feedback_id) is not str or not _ID.fullmatch(self.feedback_id):
            raise ValueError('Invalid feedback reference.')

    def payload(self):
        self.__post_init__()
        return {'format': 'snapflow-feedback-v1', 'feedback_id': self.feedback_id,
                'category': self.category, 'title': self.title, 'description': self.description}


@dataclass(frozen=True)
class SafeDiagnostics:
    """Accept only bounded counts, timings, booleans and compiled enum labels."""
    private_mode: bool = False
    analytics_enabled: bool = False
    crash_records_enabled: bool = False
    feature_states: tuple = ()
    event_counts: tuple = ()
    mean_duration_ms: float | None = None
    report_ids: tuple = ()
    resources: tuple = ()

    def payload(self):
        if any(type(value) is not bool for value in (self.private_mode, self.analytics_enabled, self.crash_records_enabled)):
            raise ValueError('Invalid diagnostic state.')
        for values, allowed, kind in ((self.feature_states, FLAGS, 'bool'),
                (self.event_counts, EVENT_TYPES, 'count'), (self.resources, _RESOURCES, 'number')):
            if type(values) is not tuple or len(values) > len(allowed):
                raise ValueError('Invalid diagnostic fields.')
            seen = set()
            for item in values:
                if type(item) is not tuple or len(item) != 2:
                    raise ValueError('Invalid diagnostic fields.')
                key, value = item
                if type(key) is not str or key not in allowed or key in seen:
                    raise ValueError('Unregistered diagnostic field.')
                seen.add(key)
                valid = type(value) is bool if kind == 'bool' else _number(value)
                if kind == 'count':
                    valid = type(value) is int and _number(value)
                if not valid:
                    raise ValueError('Invalid diagnostic value.')
        if self.mean_duration_ms is not None and not _number(self.mean_duration_ms, 7*86400000):
            raise ValueError('Invalid diagnostic timing.')
        if type(self.report_ids) is not tuple or len(self.report_ids) > 20 or any(type(x) is not str or not _ID.fullmatch(x) for x in self.report_ids):
            raise ValueError('Invalid safe report references.')
        return {'format': 'snapflow-safe-diagnostics-v1', 'app_version': APP_VERSION,
                'private_mode': self.private_mode, 'local_analytics_enabled': self.analytics_enabled,
                'local_crash_records_enabled': self.crash_records_enabled,
                'beta_features': dict(self.feature_states), 'recent_event_counts': dict(self.event_counts),
                'mean_operation_duration_ms': self.mean_duration_ms,
                'recent_error_references': list(self.report_ids), 'process_resources': dict(self.resources)}

    def preview(self):
        return _json_bytes(self.payload()).decode('utf-8')


class FeedbackService:
    def __init__(self, paths=None):
        self.paths = paths or AppPaths()

    def snapshot(self, *, config, observability=None, crash_reporter=None, resources=None, flags=None):
        analytics = getattr(config, 'local_observability_enabled', False) is True
        crashes_enabled = getattr(config, 'local_crash_reports_enabled', False) is True
        events, crash_rows = [], []
        if analytics and getattr(observability, 'store', None):
            try:
                events = observability.store.recent_events(1000)
            except Exception:
                pass
        if crashes_enabled and crash_reporter:
            try:
                crash_rows = crash_reporter.recent()
            except Exception:
                pass
        counts, durations, references = Counter(), [], []
        for row in events[:1000]:
            if type(row) is not dict or type(row.get('event_type')) is not str or row['event_type'] not in EVENT_TYPES:
                continue
            counts[row['event_type']] += 1
            duration = row.get('duration_ms')
            if _number(duration, 7*86400000):
                durations.append(duration)
        for row in crash_rows[:20]:
            reference = row.get('crash_id') if type(row) is dict else None
            if type(reference) is str and _ID.fullmatch(reference):
                references.append(reference)
        resource_values = tuple((key, value) for key, value in (resources or {}).items()
                                if key in _RESOURCES and _number(value, 1000000))
        features = tuple((key, flags.enabled(key) is True) for key in FLAGS) if flags else ()
        return SafeDiagnostics(private_mode=getattr(config, 'private_mode', False) is True,
            analytics_enabled=analytics, crash_records_enabled=crashes_enabled,
            feature_states=features, event_counts=tuple(sorted(counts.items())),
            mean_duration_ms=sum(durations)/len(durations) if durations else None,
            report_ids=tuple(references), resources=resource_values)

    @staticmethod
    def error_prefill(*, component='application', category='unexpected', reference_id=None):
        component = component if type(component) is str and component in COMPONENTS else 'application'
        category = category if type(category) is str and category in ERROR_CATEGORIES else 'unexpected'
        reference = reference_id if type(reference_id) is str and _ID.fullmatch(reference_id) else 'unavailable'
        return FeedbackReport('Bug', f'Issue in {component}',
            f'Component: {component}\nError category: {category}\nSafe reference: {reference}\n\nDescribe what happened and what you expected:')

    @staticmethod
    def validate_screenshot(source):
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice
        from PySide6.QtGui import QImageReader
        path = require_plain_path(source)
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg'} or not path.is_file():
            raise ValueError('Select a PNG or JPG image file explicitly.')
        with path.open('rb') as stream:
            payload = stream.read(8*1024*1024+1)
        if len(payload) > 8*1024*1024:
            raise ValueError('Screenshot attachments must be at most 8 MiB.')
        buffer = QBuffer()
        buffer.setData(QByteArray(payload))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setDecideFormatFromContent(True)
        size = reader.size()
        image_format = bytes(reader.format()).decode('ascii', errors='ignore').lower()
        if image_format not in {'png', 'jpeg', 'jpg'} or not size.isValid() or size.width()*size.height() > 50000000:
            raise ValueError('The selected file is not a supported image.')
        if reader.read().isNull():
            raise ValueError('The selected image could not be decoded.')
        return ('screenshot.png' if image_format == 'png' else 'screenshot.jpg'), payload

    def export(self, destination, feedback, *, diagnostics=None, screenshot=None):
        if type(feedback) is not FeedbackReport:
            raise ValueError('A validated feedback report is required.')
        feedback_bytes = _json_bytes(feedback.payload())
        if diagnostics is not None and type(diagnostics) is not SafeDiagnostics:
            raise ValueError('Only the closed safe diagnostics preview can be attached.')
        diagnostic_bytes = diagnostics.preview().encode('utf-8') if diagnostics is not None else None
        image = self.validate_screenshot(screenshot) if screenshot is not None else None
        entries = [('feedback.json', feedback_bytes)]
        if diagnostic_bytes is not None:
            entries.append(('safe_diagnostics.json', diagnostic_bytes))
        if image is not None:
            entries.append(image)
        return self._export_entries(destination, entries)

    def export_diagnostics(self, destination, safe_payload):
        if type(safe_payload) is not SafeDiagnostics:
            raise ValueError('Only the closed safe diagnostics preview can be exported.')
        return self._export_entries(destination, [('safe_diagnostics.json', safe_payload.preview().encode('utf-8'))])

    def _export_entries(self, destination, entries):
        destination = require_plain_path(destination)
        if destination.suffix.lower() != '.zip' or destination.exists():
            raise ValueError('Choose a new .zip file; existing files are never overwritten.')
        if destination.is_relative_to(self.paths.data_dir.absolute()) or destination.is_relative_to(self.paths.models_dir.absolute()):
            raise ValueError('Save feedback outside the application data and model folders.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.feedback-', suffix='.tmp', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'w+b') as stream:
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for entry in entries:
                        archive.writestr(*entry)
                stream.flush()
                os.fsync(stream.fileno())
            require_plain_path(destination)
            # Hard-link publication is atomic and fails if another writer created the target.
            os.link(temporary, destination)
        except FileExistsError:
            raise ValueError('The destination already exists. Choose a new file.') from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
