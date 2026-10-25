"""Offline browser authority and privacy checks in an explicit disposable profile."""
import json
import os
from pathlib import Path
import sys
import zipfile
from uuid import uuid4


def install_smoke_isolation():
    raw = os.environ.get('SNAPFLOW_PHASE18_PROFILE', '')
    profile = Path(raw).absolute()
    if (not raw or os.environ.get('SNAPFLOW_PHASE18_SMOKE') != '1'
            or profile != Path(os.environ.get('APPDATA', '')).absolute()
            or profile != Path(os.environ.get('LOCALAPPDATA', '')).absolute()
            or not (profile / '.snapflow-phase18-test').is_file()):
        raise ValueError('Phase 18 smoke requires an explicit disposable profile.')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in tuple(os.environ):
        if key.upper().startswith(('AI_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_',
                                   'GOOGLE_API_', 'GEMINI_', 'MISTRAL_', 'TODOIST_', 'MCP_')):
            os.environ.pop(key, None)
    def deny(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo'):
            raise PermissionError('Phase 18 smoke denies network requests.')
    sys.addaudithook(deny)
    from src.integrations import credentials
    class EmptyBackend:
        def get(self, target):
            return None
        def set(self, *args):
            raise PermissionError('Disposable smoke cannot modify credentials.')
        delete = set
    credentials.WindowsCredentialBackend = EmptyBackend
    # Profile isolation does not isolate Windows-wide hotkeys or tray UI.
    # Suppress those side effects explicitly; they are not qualified by smoke.
    from src.hotkeys import Hotkeys
    from PySide6.QtWidgets import QSystemTrayIcon
    Hotkeys.register = lambda self, *args, **kwargs: None
    QSystemTrayIcon.show = lambda self: None
    QSystemTrayIcon.showMessage = lambda self, *args, **kwargs: None


def _must_refuse(function, errors=(PermissionError, ValueError)):
    try:
        function()
    except errors:
        return
    raise AssertionError('An authorization boundary accepted an unsafe operation.')


def run_safety_checks(paths):
    """Exercise real authority/journal/export code; no browser or AI is simulated."""
    from src.backup import BackupService
    from src.beta.feedback import FeedbackReport, FeedbackService
    from src.browser.manager import BrowserManager
    from src.browser.protocol import envelope
    from src.config import Config, save_config
    from src.network_policy import NetworkPolicyService
    from src.reliability.journal import ExecutionJournal
    from src.reliability.policy import SafetyPolicy
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    # This helper's caller supplies disposable paths; never call on a real profile.
    config = Config(private_mode=True, ai_execution_mode='local_only',
                    automatic_update_checks=False, onboarding_complete=True)
    if not paths.config_file.exists():
        save_config(config, paths.config_file)
    gates = {}
    safe = SafetyPolicy()
    safe.set_safe_mode(True)
    extension = 'chrome-extension://' + 'a' * 32 + '/'
    _must_refuse(lambda: BrowserManager((extension,), safety_policy=safe).enable())
    gates['safe_mode_blocks'] = 'PASS'
    policy = NetworkPolicyService(private_mode=True)
    _must_refuse(lambda: policy.require_allowed('https://example.test', 'cloud_ai'))
    journal = ExecutionJournal(paths.recovery_database)
    private = [True]
    manager = BrowserManager((extension,), journal=journal, safety_policy=SafetyPolicy(),
                             actions_allowed=lambda: not private[0])
    manager.enable()
    connection = manager.exchange(extension, envelope('hello', {
        'browser_kind': 'chrome', 'extension_version': '0.5.0'}))['connection_id']
    identity = dict(tab_id=7, frame_id=0, document_id='owned-release-fixture',
                    origin='https://example.test')
    def call(kind, payload, session=None):
        return manager.exchange(extension, envelope(kind, payload,
            connection_id=connection, session_id=session))
    session = call('authorize', dict(identity=identity, page_title='Synthetic release check',
        scope='read', access_mode='active_tab'))['session_id']
    sentinel = 'SYNTHETIC-BROWSER-CONTENT-' + uuid4().hex
    import time
    call('context_snapshot', dict(snapshot_id=uuid4().hex, identity=identity,
        page_title='Synthetic release check', relevant_text=sentinel, headings=[],
        interactive_elements=[], forms=[], links=[], tables=[],
        captured_at=int(time.time() * 1000), warnings=[]), session)
    call('interact', dict(identity=identity), session)
    prepare = dict(identity=identity,
        spec=dict(action_type='set_text', target=dict(role='textbox', name='Search'), value=sentinel),
        before=dict(signature='a' * 64, state=''))
    _must_refuse(lambda: call('action_prepare', prepare, session))
    gates['private_mode_blocks'] = 'PASS'
    # Synthetic authority only. No script or browser receives this proposal.
    private[0] = False
    action = call('action_prepare', prepare, session)['payload']['action']
    call('action_claim', dict(identity=identity, action_id=action['action_id'],
         fingerprint=action['fingerprint']), session)
    # Simulate a process loss after claim by opening a new journal owner.
    recovered = ExecutionJournal(paths.recovery_database)
    if recovered.recover_interrupted() != 1:
        raise AssertionError('Claimed interruption was not recoverable.')
    import sqlite3
    _must_refuse(lambda: recovered.claim_browser(action['action_id']), (sqlite3.IntegrityError,))
    gates['interruption_no_replay'] = 'PASS'
    manager.disable()
    if manager.connections() or manager.sessions() or manager.context.snapshots() or manager.actions.pending:
        raise AssertionError('Stop retained page authority or content.')
    _must_refuse(lambda: call('session_status', dict(identity=identity), session))
    gates['stop_revokes'] = 'PASS'
    records = recovered.records()
    if sentinel in str(records) or any(row['status'] not in ('interrupted', 'unknown') for row in records):
        raise AssertionError('Recovery retained content or replayable state.')
    gates['journal_content_free'] = 'PASS'
    # A fabricated site preference must neither export authority nor page origins.
    preferences = paths.data_dir / 'browser_site_permissions.json'
    preferences.write_text(json.dumps({'version': 1, 'sites': [
        {'extension_origin': extension, 'origin': identity['origin']}]}), encoding='utf8')
    destination = paths.data_dir.parent / ('phase18-exports-' + uuid4().hex)
    destination.mkdir()
    backup = BackupService(paths).export_backup(destination / 'backup.zip')
    feedback = FeedbackService(paths)
    diagnostics = feedback.snapshot(config=config)
    support = feedback.export(destination / 'support.zip',
        FeedbackReport('Bug', 'Synthetic release check', 'User-authored synthetic description'),
        diagnostics=diagnostics)
    for name, archive in (('backup_content_free', backup), ('support_content_free', support)):
        with zipfile.ZipFile(archive) as stream:
            payload = b'\n'.join(stream.read(member) for member in stream.namelist())
            if any(value.encode() in payload for value in (sentinel, identity['origin'], extension)):
                raise AssertionError('Browser content or authority escaped to an export.')
        gates[name] = 'PASS'
    return {'status': 'PASS', 'gates': gates, 'synthetic_content_only': True,
            'installed_browser_acceptance': False, 'live_inference': False}


def run_packaged_browser_smoke(app, owner):
    report, code = {'status': 'FAIL', 'gates': {}}, 1
    try:
        browser = owner.browser_controller
        if owner.hotkeys.active_id is not None:
            raise AssertionError('Smoke must not register a global desktop hotkey.')
        report['gates']['no_native_hotkey'] = 'PASS'
        if browser.service is not None or browser.dialog is not None:
            raise AssertionError('Browser Companion must remain lazy and disabled at startup.')
        report['gates']['actual_app_default_off'] = 'PASS'
        service = browser.ensure_service()
        if service.enabled or service.ipc is not None or service.timer.isActive():
            raise AssertionError('Inspecting the companion enabled authority.')
        # The acceptance launch uses actual application Safe Mode and Private Mode.
        if not service.policy.safe_mode or not owner.config.private_mode:
            raise AssertionError('Run smoke with Safe Mode and Private Mode.')
        if service.actions_allowed() or not owner.client.policy.private_mode:
            raise AssertionError('Actual application Private Mode permits browser actions.')
        report['gates']['actual_app_private_mode'] = 'PASS'
        _must_refuse(lambda: service.enable(('chrome-extension://' + 'a' * 32 + '/',)))
        if service.enabled or service.ipc is not None:
            raise AssertionError('Safe Mode created a browser pipe.')
        report['gates']['actual_app_safe_mode'] = 'PASS'
        from src.paths import AppPaths
        result = run_safety_checks(AppPaths(data_dir=owner.paths.data_dir / 'browser-smoke',
            resource_root=owner.paths.resource_root))
        report.update({key: value for key, value in result.items() if key != 'gates'})
        report['gates'].update(result['gates'])
        if getattr(sys, 'frozen', False):
            if not Path(sys.executable).with_name('SnapFlowBrowserHost.exe').is_file():
                raise AssertionError('Companion host missing from the frozen payload.')
            for kind in ('chrome', 'edge'):
                if not (owner.paths.resource_root / 'browser-extension' / kind / 'manifest.json').is_file():
                    raise AssertionError('Built extension missing from the frozen payload.')
            report['gates']['actual_app_companion_assets'] = 'PASS'
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        code = 0
    except Exception as exc:
        report['status'] = 'FAIL'
        report['error_type'] = type(exc).__name__
    finally:
        owner.paths.data_dir.joinpath('phase18_smoke.json').write_text(
            json.dumps(report, indent=2), encoding='utf8')
        owner.quit()
        if code:
            app.exit(code)
