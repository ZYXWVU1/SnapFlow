"""Offline, disposable real-UIA-to-Agent acceptance for the actual application."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def install_smoke_isolation():
    profile = Path(os.environ.get('SNAPFLOW_PHASE16_PROFILE', '')).absolute()
    if (os.environ.get('SNAPFLOW_PHASE16_SMOKE') != '1' or profile != Path(os.environ.get('APPDATA', '')).absolute() or
            profile != Path(os.environ.get('LOCALAPPDATA', '')).absolute() or not (profile / '.snapflow-phase16-test').is_file()):
        raise ValueError('Missing disposable desktop action verification profile.')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in tuple(os.environ):
        if key.upper().startswith(('AI_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_', 'GOOGLE_API_', 'GEMINI_', 'MISTRAL_')):
            os.environ.pop(key, None)
    def deny(event, args):
        if event == 'socket.connect':
            raise PermissionError('Desktop action qualification denies network sockets.')
    sys.addaudithook(deny)
    from src.integrations import credentials
    class EmptyBackend:
        def get(self, target):
            return None
        def set(self, target, secret):
            raise PermissionError('Disposable action smoke cannot save credentials.')
        def delete(self, target):
            raise PermissionError('Disposable action smoke cannot change credentials.')
    credentials.WindowsCredentialBackend = EmptyBackend


class SyntheticPlanner:
    policy = SimpleNamespace(private_mode=True)
    def request_text(self, prompt, **kwargs):
        if kwargs.get('cancelled') and kwargs['cancelled']():
            raise InterruptedError()
        payload = json.loads(prompt)
        capability = next(c for c in payload['capabilities'] if c['capability_id'].startswith('desktop:toggle:') and c['name'].endswith('Dark Mode'))
        result = json.dumps({'goal_summary': 'Enable the synthetic Dark Mode checkbox', 'steps': [{
            'step_id': 'setting', 'title': 'Enable Dark Mode', 'step_type': 'desktop_action',
            'capability_id': capability['capability_id'], 'input_bindings': {'value': True}}]})
        if kwargs.get('validator'):
            kwargs['validator'](result)
        return result


def run_packaged_action_smoke(app, owner):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton, QDialog
    from src.desktop_actions.models import DesktopActionFence
    from src.desktop_actions.synthetic_app import SyntheticActionWindow
    from src.reliability.policy import get_safety_policy
    report = {'schema_version': 1, 'status': 'FAIL', 'frozen': bool(getattr(sys, 'frozen', False)),
        'external_sockets': 'denied', 'synthetic_content_only': True, 'gates': {}, 'measurements': {}}
    remaining = set(filter(None, os.environ.get('SNAPFLOW_PHASE16_GATES', '').split(',')))
    def requested(name):
        return not remaining or name in remaining
    def passed(name):
        if requested(name):
            report['gates'][name] = 'PASS'
    started, code, window = time.perf_counter(), 1, None
    try:
        actions = owner.desktop_actions_controller
        # Only this isolated owned fixture shares the actual application's PID.
        actions.service.allow_owned_test_window = True
        if requested('default_off'):
            assert actions.service.fence is None and not owner.config.controlled_desktop_actions_enabled
            assert not owner.beta_features.enabled('controlled_computer_use_v1')
            passed('default_off')
        if requested('native_asset'):
            assert Path(__file__).with_name('uia_actions.ps1').is_file()
            passed('native_asset')
        def inspect_settings():
            assert actions.dialog is not None
            actions.dialog.reject()
        if requested('settings_wiring'):
            assert hasattr(owner.main_window.pages['settings'], 'desktop_actions_requested')
            QTimer.singleShot(100, inspect_settings)
            owner.main_window.pages['settings'].desktop_actions_requested.emit()
            passed('settings_wiring')
        owner.config = replace(owner.config, controlled_desktop_actions_enabled=True, private_mode=True,
            ai_execution_mode='local_only', local_ai_model='synthetic-planner', desktop_ui_policy='session')
        owner.beta_features.save({'controlled_computer_use_v1': True, 'agent_missions_v1': True}, experiments_enabled=False)
        owner.agent_controller.refresh_enabled()
        owner.agent_controller.services.client = SyntheticPlanner()
        owner.main_window.open_page('agent')
        owner.main_window.show()
        owner.main_window.activateWindow()
        owner.main_window.raise_()
        app.processEvents()
        window = SyntheticActionWindow()
        window.show()
        window.showNormal()
        window.activateWindow()
        window.raise_()
        app.processEvents()
        report['stage'] = 'owned_window_ready'
        from src.desktop_context.active_window import ActiveWindowProvider
        owned_deadline = time.monotonic() + 3
        while ActiveWindowProvider(title_allowed=lambda app: False)().hwnd != int(window.winId()) and time.monotonic() < owned_deadline:
            window.showNormal()
            window.activateWindow()
            window.raise_()
            app.processEvents()
            time.sleep(.02)
        assert ActiveWindowProvider(title_allowed=lambda app: False)().hwnd == int(window.winId())
        desktop = owner.desktop_controller.service
        desktop.permissions.configure(ui='session')
        desktop.permissions.grant_session(('ui',))
        with ThreadPoolExecutor(max_workers=1) as pool:
            def pump(future):
                ticks = 0
                deadline = time.monotonic() + 45
                while not future.done():
                    if time.monotonic() > deadline:
                        actions.stop()
                        raise TimeoutError('Bounded action acceptance timed out.')
                    app.processEvents()
                    ticks += 1
                    time.sleep(.005)
                report['measurements']['qt_ticks'] = report['measurements'].get('qt_ticks', 0) + ticks
                return future.result()
            snapshot = pump(pool.submit(desktop.capture, requested_sources=('metadata', 'ui')))
            if requested('desktop_context_to_semantic_target'):
                assert snapshot.ui_elements and any(e.name == 'Dark Mode' for e in snapshot.ui_elements)
                assert not any('FABRICATED_PASSWORD' in (e.value or '') for e in snapshot.ui_elements)
                passed('desktop_context_to_semantic_target')
            fence = DesktopActionFence(snapshot.active_window.process_id, snapshot.active_window.process_name,
                snapshot.active_window.hwnd, 'native_scope')
            # The owned synthetic process is the sole local exception for this fabricated qualification.
            actions.service.local_processes = (snapshot.active_window.process_name,)
            pump(pool.submit(actions.provider.prepare, fence, snapshot))
            sources = desktop.sources(snapshot.snapshot_id)
            session = pump(pool.submit(owner.agent_controller.service.create,
                {'goal': 'Enable the synthetic Dark Mode checkbox', 'include_current': False,
                 'constraints': {'allow_local_writes': True, 'allow_remote_ai': False, 'max_steps': 3}},
                desktop_sources=sources))
            agent = owner.agent_controller
            agent.session_id = session.session_id
            agent.refresh()
            plan = agent.store.get_plan(session.current_plan_id)
            step, inputs = agent.service.resolved_inputs(session.session_id, 'setting')
            passed('agent_ephemeral_plan')
            # Real review has a visible Mission Control parent. Closing a modal
            # with a hidden tray-only parent can restore a foreign foreground.
            owner.main_window.open_page('agent')
            owner.main_window.show()
            owner.main_window.activateWindow()
            owner.main_window.raise_()
            app.processEvents()
            # Exercise the actual queued preview dialog/highlight/approval wiring.
            report['stage'] = 'preview'
            original_preview = actions.provider.preview
            def observed_preview(*args, **kwargs):
                preview = original_preview(*args, **kwargs)
                report['review'] = {'allowed': preview.allowed, 'risk': preview.risk_level,
                    'resolution': preview.resolution.status}
                return preview
            actions.provider.preview = observed_preview
            original_guard = agent._guard
            def observed_guard(*args, **kwargs):
                try:
                    return original_guard(*args, **kwargs)
                except Exception as error:
                    report['guard_error_type'] = type(error).__name__
                    raise
            agent._guard = observed_guard
            original_approve = actions.provider.approve
            def observed_approve(*args, **kwargs):
                try:
                    return original_approve(*args, **kwargs)
                except Exception as error:
                    report['approval_error_type'] = type(error).__name__
                    raise
            actions.provider.approve = observed_approve
            original_step_approve = agent.service.approve_step
            def observed_step_approve(*args, **kwargs):
                try:
                    return original_step_approve(*args, **kwargs)
                except Exception as error:
                    import traceback
                    report['step_approval_error_type'] = type(error).__name__
                    report['step_approval_trace'] = [{'function': frame.name, 'line': frame.lineno}
                        for frame in traceback.extract_tb(error.__traceback__)[-6:]]
                    raise
            agent.service.approve_step = observed_step_approve
            approved = []
            def approve_dialog():
                if actions.dialog and actions.dialog.isVisible():
                    actions.dialog.activateWindow()
                    actions.dialog.raise_()
                    if ActiveWindowProvider(title_allowed=lambda app: False)().process_id != os.getpid():
                        QTimer.singleShot(30, approve_dialog)
                        return
                    assert actions.dialog.windowTitle().startswith('Desktop Action Preview')
                    button = next(b for b in actions.dialog.findChildren(QPushButton) if b.text() == 'Approve Once')
                    assert button.isEnabled()
                    approved.append(True)
                    report['visible_preview_clicked'] = True
                    button.click()
                elif not approved:
                    QTimer.singleShot(30, approve_dialog)
            QTimer.singleShot(30, approve_dialog)
            agent.approve_step(step.step_id)
            deadline = time.monotonic() + 15
            while actions.worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            assert approved and actions.worker is None
            report['stage'] = 'desktop_grant'
            assert actions.provider.grants
            assert not actions.outlines
            passed('preview_highlight_exact_approval')
            window.activateWindow()
            window.raise_()
            app.processEvents()
            foreground_deadline = time.monotonic() + 3
            while ActiveWindowProvider(title_allowed=lambda app: False)().hwnd != fence.allowed_window and time.monotonic() < foreground_deadline:
                window.activateWindow()
                window.raise_()
                app.processEvents()
                time.sleep(.02)
            assert ActiveWindowProvider(title_allowed=lambda app: False)().hwnd == fence.allowed_window
            report['stage'] = 'execution'
            original_execute = actions.service.execute
            def observed_execute(*args, **kwargs):
                result = original_execute(*args, **kwargs)
                report['desktop_execution'] = {'status': result.status,
                    'warnings': list(result.warnings),
                    'native_error_code': actions.service.provider.last_error_code}
                return result
            actions.service.execute = observed_execute
            outcome = pump(pool.submit(agent.service.execute, session.session_id))
            action_state = agent.store.step_states(plan.plan_id)['setting']
            action_output = action_state.get('output') or {}
            report['execution'] = {'mission_status': outcome['status'],
                'step_status': action_state['status'],
                'desktop_status': action_output.get('status'),
                'desktop_verified': action_output.get('desktop_verified'),
                'native_error_code': actions.service.provider.last_error_code,
                'target_foreground': actions.service.provider.foreground_matches(fence),
                'warnings': list(action_output.get('warnings', ())) }
            assert outcome['status'] == 'completed'
            assert window.setting.isChecked()
            state = agent.store.step_states(plan.plan_id)['setting']
            assert state['output']['desktop_verified'] is True
            passed('native_agent_action_verified')
            assert all(row['status'] == 'completed' for row in owner.execution_journal.records())
            assert b'FABRICATED_PASSWORD' not in agent.store.path.read_bytes()
            passed('journal_private_receipt')
            actions.stop()
            assert actions.service.fence is None and not actions.provider.grants and not actions.outlines
            passed('stop_cleanup')
            policy = get_safety_policy()
            policy.set_safe_mode(True)
            assert not policy.allows('desktop_actions') and not policy.allows('desktop_input_fallback')
            policy.set_safe_mode(False)
            passed('safe_mode')
        report['status'], code = 'PASS', 0
    except Exception as error:
        import traceback
        report['error_type'] = type(error).__name__
        report['error_trace'] = [{'function': frame.name, 'line': frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-6:]]
    finally:
        if window:
            window.close()
        report['measurements']['duration_seconds'] = round(time.perf_counter() - started, 3)
        owner.paths.data_dir.joinpath('phase16_smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)
        owner.quit()
        app.exit(code)
