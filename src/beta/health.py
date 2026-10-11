"""Offline health checks; never probe providers, replay actions, or export content."""
from contextlib import closing
from dataclasses import dataclass
import json
import shutil
import sqlite3
import tempfile
import time
from types import MappingProxyType

from src.config import Config
from src.paths import AppPaths
from .flags import require_plain_path
from .feedback import _ID, _number

_ISSUES = MappingProxyType({
    'checks_unverified': ('system', 'warning', 'Some features have not been verified.',
        'Review untested items in Advanced. Local checks do not validate provider calls or backup restoration.'),
    'data_unwritable': ('system', 'error', 'App data is not writable.', 'Check folder permissions and available space.'),
    'disk_low': ('system', 'warning', 'Less than 512 MiB is free.', 'Free disk space before capturing or importing models.'),
    'disk_unknown': ('system', 'warning', 'Free disk space could not be checked.', 'Check the app data drive in File Explorer.'),
    'database_integrity': ('storage', 'error', 'The local database needs attention.', 'Preserve the database and restore a verified backup through Backup & Restore.'),
    'database_missing': ('storage', 'warning', 'The local database has not been created.', 'Open Visual Memory after a normal startup; existing files are preserved.'),
    'backup_missing': ('storage', 'warning', 'No local backup package was found.', 'Create a backup in Backup & Restore.'),
    'settings_invalid': ('system', 'error', 'Settings could not be validated.', 'Open Settings or restart in Safe Mode; preserve the original file.'),
    'fts_unavailable': ('storage', 'warning', 'The Memory search index is unavailable or incomplete.', 'Review Visual Memory search and use its index repair action.'),
    'embedding_incompatible': ('storage', 'warning', 'The embedding index differs from the selected manifest.', 'Rebuild the semantic index for the selected embedding model.'),
    'embedding_incomplete': ('storage', 'warning', 'Some current Memory revisions are not indexed for the selected model.', 'Rebuild the semantic index for the selected embedding model.'),
    'embedding_unverified': ('storage', 'warning', 'Embedding compatibility could not be verified.', 'Choose a local embedding model and review the Memory index.'),
    'local_runtime_error': ('ai_runtime', 'error', 'The local runtime needs attention.', 'Open Local Models, stop the runtime and load the selected model manually.'),
    'local_runtime_unverified': ('ai_runtime', 'warning', 'Local runtime readiness has not been verified.', 'Open Local Models to review or explicitly start the runtime.'),
    'cloud_unverified': ('ai_runtime', 'warning', 'Cloud provider health has not been tested.', 'Review AI settings; a provider test requires a separate explicit action.'),
    'recovery_review': ('recovery', 'warning', 'Interrupted actions need manual review.', 'Check the external service before acknowledging; acknowledgement never replays an action.'),
    'recovery_unverified': ('recovery', 'warning', 'Recovery records could not be checked.', 'Restart in Safe Mode and review local diagnostics.'),
    'previous_shutdown': ('recovery', 'warning', 'The previous session did not finish cleanly.', 'Review interrupted actions before continuing work.'),
    'mcp_attention': ('mcp', 'warning', 'Extensions need connection or permission review.', 'Open Extensions to review authentication, permissions and connection status.'),
    'mcp_unverified': ('mcp', 'warning', 'Extension status could not be checked.', 'Open Extensions to inspect the local profiles.'),
    'models_unverified': ('storage', 'warning', 'Model storage size could not be checked.', 'Inspect Local Models and available disk space.'),
})


@dataclass(frozen=True)
class HealthIssue:
    code: str

    def __post_init__(self):
        if type(self.code) is not str or self.code not in _ISSUES:
            raise ValueError('Unregistered health issue.')

    @property
    def component(self): return _ISSUES[self.code][0]
    @property
    def severity(self): return _ISSUES[self.code][1]
    @property
    def message(self): return _ISSUES[self.code][2]
    @property
    def action(self): return _ISSUES[self.code][3]

    def payload(self):
        return dict(code=self.code, component=self.component, severity=self.severity,
                    message=self.message, recommended_action=self.action)


@dataclass(frozen=True)
class HealthReport:
    status: str
    checks: object
    issues: tuple

    def payload(self):
        return dict(format='snapflow-health-v1', status=self.status, checks=dict(self.checks),
                    issues=[issue.payload() for issue in self.issues])


class HealthCenterService:
    def __init__(self, paths=None, config=None, *, runtime_manager=None, mcp_manager=None,
                 journal=None, observability=None, embedding_manifest=None, previous_session_unclean=False):
        self.paths, self.config = paths or AppPaths(), config or Config()
        self.runtime_manager, self.mcp_manager = runtime_manager, mcp_manager
        self.journal, self.observability = journal, observability
        self.embedding_manifest = embedding_manifest
        self.previous_session_unclean = previous_session_unclean is True

    def run(self):
        checks, codes = {}, []
        def issue(code):
            if code not in codes:
                codes.append(code)
        try:
            require_plain_path(self.paths.data_dir)
            self.paths.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='.health-', dir=self.paths.data_dir):
                pass
            checks['app_data'] = 'writable'
        except (OSError, ValueError):
            checks['app_data'] = 'unwritable'
            issue('data_unwritable')
        try:
            free = shutil.disk_usage(self.paths.data_dir).free
            checks['free_disk_mib'] = free // (1024*1024)
            if free < 512*1024*1024:
                issue('disk_low')
        except OSError:
            checks['free_disk_mib'] = None
            issue('disk_unknown')
        checks['settings'] = 'valid'
        try:
            self.config.__post_init__()
            require_plain_path(self.paths.config_file)
            if self.paths.config_file.exists():
                if self.paths.config_file.stat().st_size > 1024*1024:
                    raise ValueError('Oversized settings.')
                payload = json.loads(self.paths.config_file.read_text(encoding='utf-8'))
                if type(payload) is not dict:
                    raise ValueError('Invalid settings.')
                Config(**payload)
        except (ValueError, TypeError, OSError):
            checks['settings'] = 'invalid'
            issue('settings_invalid')
        self._storage(checks, issue)
        try:
            require_plain_path(self.paths.backup_dir)
            backups = [p for p in self.paths.backup_dir.glob('*.zip')
                       if p.is_file() and not p.is_symlink() and not getattr(p.stat(), 'st_file_attributes', 0) & 0x400]
            checks['backup_packages'] = min(1000000, len(backups))
            checks['backup_integrity'] = 'not_verified'
            if not backups:
                issue('backup_missing')
        except (OSError, ValueError):
            checks['backup_packages'] = 0
            checks['backup_integrity'] = 'not_verified'
            issue('backup_missing')
        mode = getattr(self.config, 'ai_execution_mode', 'cloud_only')
        checks['ai_mode'] = mode if mode in {'automatic', 'prefer_local', 'local_only', 'cloud_only', 'ask_every_time'} else 'unknown'
        checks['cloud_provider'] = 'blocked_private_mode' if self.config.private_mode else 'not_verified'
        checks['cloud_configuration'] = 'configured' if self.config.ai_base_url or self.config.ai_model else 'defaults'
        if not self.config.private_mode and mode != 'local_only':
            issue('cloud_unverified')
        state = 'not_verified'
        if self.runtime_manager is not None:
            try:
                raw = self.runtime_manager.state
                value = getattr(raw, 'state', None)
                if value in {'stopped', 'starting', 'loading_model', 'ready', 'busy', 'stopping', 'error'}:
                    state = value
                checks['model_loaded'] = bool(getattr(raw, 'model_id', None)) and state in {'ready', 'busy'}
            except Exception:
                pass
        checks['local_runtime'] = state
        checks.setdefault('model_loaded', False)
        if state == 'error':
            issue('local_runtime_error')
        elif mode != 'cloud_only' and state not in {'ready', 'busy'}:
            issue('local_runtime_unverified')
        self._ai_outcomes(checks)
        self._mcp(checks, issue)
        checks['previous_shutdown'] = self._previous_shutdown()
        if checks['previous_shutdown'] == 'unclean':
            issue('previous_shutdown')
        try:
            checks['recovery_review_count'] = len(self.review_items())
            if checks['recovery_review_count']:
                issue('recovery_review')
        except Exception:
            checks['recovery_review_count'] = None
            issue('recovery_unverified')
        issues = tuple(HealthIssue(code) for code in codes)
        if any(value in ('not_verified', 'unverified') for value in checks.values() if type(value) is str):
            issue('checks_unverified')
            issues = tuple(HealthIssue(code) for code in codes)
        status = 'error' if any(x.severity == 'error' for x in issues) else 'warning' if issues else 'healthy'
        return HealthReport(status, MappingProxyType(checks), issues)

    def _storage(self, checks, issue):
        checks['database'], checks['search_index'] = 'not_created', 'not_verified'
        checks['embedding_index'] = 'disabled' if not self.config.semantic_search_enabled and self.embedding_manifest is None else 'not_verified'
        try:
            path = require_plain_path(self.paths.learning_database)
            for suffix in ('-wal', '-shm', '-journal'):
                require_plain_path(path.with_name(path.name + suffix))
            if not path.exists():
                issue('database_missing')
            else:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1.0)) as db:
                    db.execute('PRAGMA query_only=ON')
                    deadline = time.monotonic() + 3
                    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
                    if db.execute('PRAGMA quick_check(1)').fetchone() != ('ok',):
                        raise sqlite3.DatabaseError('Integrity check failed.')
                    checks['database'] = 'healthy'
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if {'memory_fts', 'memory_records'} <= tables:
                        counts = db.execute('SELECT (SELECT count(*) FROM memory_fts),(SELECT count(*) FROM memory_records)').fetchone()
                        checks['search_index'] = 'available' if counts[0] == counts[1] else 'incomplete'
                    if checks['search_index'] != 'available':
                        issue('fts_unavailable')
                    if self.embedding_manifest is not None:
                        if {'memory_embeddings', 'memory_records'} <= tables:
                            manifest = self.embedding_manifest
                            total = db.execute('SELECT count(*) FROM memory_records').fetchone()[0]
                            matched = db.execute('SELECT count(*) FROM memory_embeddings e JOIN memory_records m ON m.id=e.memory_id '
                                'WHERE e.namespace=? AND e.dimension=? AND e.revision=m.updated_at',
                                (manifest.namespace, manifest.dimension)).fetchone()[0]
                            invalid_dimension = db.execute('SELECT count(*) FROM memory_embeddings WHERE namespace=? AND dimension!=?',
                                                           (manifest.namespace, manifest.dimension)).fetchone()[0]
                            checks['embedding_index'] = ('incompatible' if invalid_dimension else 'empty' if total == 0
                                                        else 'compatible' if matched == total else 'incomplete')
                            if invalid_dimension:
                                issue('embedding_incompatible')
                            elif matched < total:
                                issue('embedding_incomplete')
                        else:
                            issue('embedding_unverified')
                    elif self.config.semantic_search_enabled:
                        issue('embedding_unverified')
        except (OSError, ValueError, sqlite3.Error, AttributeError):
            checks['database'] = 'needs_attention'
            issue('database_integrity')
        checks['model_storage_mib'] = 0
        try:
            require_plain_path(self.paths.models_dir)
            size, seen = 0, 0
            for path in self.paths.models_dir.rglob('*'):
                require_plain_path(path)
                seen += 1
                if seen > 10000:
                    raise ValueError('Model storage scan limit reached.')
                if path.is_file():
                    size += path.stat().st_size
            checks['model_storage_mib'] = size // (1024*1024)
        except (OSError, ValueError):
            checks['model_storage_mib'] = None
            issue('models_unverified')

    def _previous_shutdown(self):
        if self.previous_session_unclean:
            return 'unclean'
        session = getattr(self.observability, 'session', None)
        if session is None:
            return 'not_verified'
        if session.previous_session_unclean:
            return 'unclean'
        try:
            prior = next((row for row in self.observability.store.sessions()
                          if row.get('session_id') != session.session_id), None)
            if prior is not None and prior.get('status') == 'clean' and prior.get('clean_shutdown') == 1:
                return 'clean'
        except Exception:
            pass
        return 'not_verified'

    def _mcp(self, checks, issue):
        counts = dict(mcp_profiles=0, mcp_connected=0, mcp_unhealthy=0, mcp_permission_review=0)
        if self.mcp_manager is not None:
            try:
                profiles = self.mcp_manager.storage.list_profiles()
                counts['mcp_profiles'] = len(profiles)
                for profile in profiles:
                    status = self.mcp_manager.state(profile.id).status
                    counts['mcp_connected'] += status == 'connected'
                    counts['mcp_unhealthy'] += status in {'error', 'needs_authentication'}
                    counts['mcp_permission_review'] += status == 'permission_review_required'
                if counts['mcp_unhealthy'] or counts['mcp_permission_review']:
                    issue('mcp_attention')
            except Exception:
                issue('mcp_unverified')
        checks.update(counts)
        checks['mcp_server'] = 'not_verified'
        checks['integrations'] = 'not_verified'

    def _ai_outcomes(self, checks):
        checks.update(weekly_ai_request_count=None, weekly_ai_success_percent=None, recent_ai_fallbacks_sample=None)
        if not self.config.local_observability_enabled or getattr(self.observability, 'store', None) is None:
            return
        try:
            total, successes = 0, 0
            for row in self.observability.store.daily_metrics(7):
                if type(row) is not dict or row.get('event_type') != 'ai_request_completed':
                    continue
                count, success_count = row.get('count'), row.get('success_count')
                if type(count) is int and type(success_count) is int and _number(count) and 0 <= success_count <= count:
                    total += count
                    successes += success_count
            checks['weekly_ai_request_count'] = total
            checks['weekly_ai_success_percent'] = round(100*successes/total, 1) if total else None
            fallback_count = 0
            for row in self.observability.store.recent_events(1000):
                if type(row) is not dict or row.get('event_type') != 'ai_request_completed':
                    continue
                properties = row.get('properties')
                if type(properties) is dict and properties.get('fallback') is True:
                    fallback_count += 1
            checks['recent_ai_fallbacks_sample'] = fallback_count
        except Exception:
            pass

    def review_items(self):
        if self.journal is None:
            raise ValueError('Recovery journal availability has not been verified.')
        return tuple({'reference_id': row['invocation_id'], 'status': row['status']}
                     for row in self.journal.pending_review()
                     if type(row.get('invocation_id')) is str and _ID.fullmatch(row['invocation_id'])
                     and row.get('status') in {'interrupted', 'unknown'})

    def acknowledge_review(self, reference_id):
        if self.journal is None or not any(row['reference_id'] == reference_id for row in self.review_items()):
            raise ValueError('Choose an unresolved recovery reference to acknowledge.')
        self.journal.acknowledge(reference_id)
