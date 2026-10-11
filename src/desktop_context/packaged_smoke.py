"""Isolated frozen-native Phase 15 checks against synthetic owned UI only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

GATES = ('default_off', 'native_assets', 'native_uia_password', 'selection_only_boundary',
    'filtered_preview', 'agent_temporary_history', 'local_suggestion', 'stop_cleanup', 'safe_mode')


def install_smoke_isolation():
    profile = Path(os.environ.get('SNAPFLOW_PHASE15_PROFILE', '')).absolute()
    if (os.environ.get('SNAPFLOW_PHASE15_SMOKE') != '1' or
            profile != Path(os.environ.get('APPDATA', '')).absolute() or
            profile != Path(os.environ.get('LOCALAPPDATA', '')).absolute() or
            not (profile / '.snapflow-phase15-test').is_file()):
        raise ValueError('Missing disposable Desktop Context smoke profile.')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in tuple(os.environ):
        if key.upper().startswith(('AI_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_', 'GOOGLE_API_', 'GEMINI_', 'MISTRAL_')):
            os.environ.pop(key, None)
    def deny_sockets(event, args):
        if event == 'socket.connect':
            raise PermissionError('Desktop smoke denies all socket connections.')
    sys.addaudithook(deny_sockets)
    from src.integrations import credentials
    class EmptyBackend:
        def get(self, target):
            return None
        def set(self, target, secret):
            raise PermissionError('Disposable desktop smoke cannot save credentials.')
        def delete(self, target):
            raise PermissionError('Disposable desktop smoke cannot change credentials.')
    credentials.WindowsCredentialBackend = EmptyBackend


def run_packaged_desktop_smoke(app, controller):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPlainTextEdit
    from src.agent.models import AgentSession, AgentPlan, AgentStep
    from src.desktop_context.models import ActiveWindowContext
    from src.desktop_context.uia import UIAutomationContextProvider
    from src.proactive.engine import ProactiveEngine
    report = {'schema_version': 1, 'status': 'FAIL', 'external_sockets': 'denied',
        'synthetic_content_only': True, 'gates': {}, 'measurements': {}, 'live_inference': 'NOT VERIFIED'}
    widget = None
    code = 1
    def passed(gate):
        report['gates'][gate] = 'PASS'
    try:
        desktop = controller.desktop_controller
        assert not desktop.engine.active and not desktop.timer.isActive() and desktop.service.latest is None
        assert not desktop.permissions.grants and desktop.permissions.policies['clipboard'] == 'disabled'
        passed('default_off')
        assert Path(__file__).with_name('uia_reader.ps1').is_file()
        passed('native_assets')
        widget = QWidget()
        widget.setWindowTitle('SnapFlow synthetic native package fixture')
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel('Synthetic assignment requirements due tomorrow'))
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setAccessibleName('Password')
        password.setText('DO-NOT-CAPTURE-SYNTHETIC-SECRET')
        layout.addWidget(password)
        editor = QPlainTextEdit('selected public text\nUNSELECTED-SYNTHETIC-PARAGRAPH')
        layout.addWidget(editor)
        widget.show()
        widget.activateWindow()
        editor.setFocus()
        cursor = editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(8, cursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        app.processEvents()
        target = ActiveWindowContext(int(widget.winId()), os.getpid(), 'python.exe', 'Synthetic fixture',
            'Synthetic assignment fixture')
        def pump(future):
            ticks = 0
            while not future.done():
                app.processEvents()
                time.sleep(.005)
                ticks += 1
            return future.result(), ticks
        with ThreadPoolExecutor(max_workers=1) as executor:
            started = time.monotonic()
            result, ticks = pump(executor.submit(UIAutomationContextProvider(timeout_seconds=8).capture,
                target, include_ui=True, include_selection=True))
            report['measurements'].update(uia_seconds=time.monotonic() - started, qt_ticks=ticks,
                elements=len(result.elements), selection_available=bool(result.selected_text), warnings=list(result.warnings))
            assert result.elements and 'DO-NOT-CAPTURE-SYNTHETIC-SECRET' not in str(result)
            assert any('Synthetic assignment requirements' in (e.name or '') for e in result.elements)
            passed('native_uia_password')
            selection, _ = pump(executor.submit(UIAutomationContextProvider(timeout_seconds=8).capture,
                target, include_ui=False, include_selection=True))
            assert 'UNSELECTED-SYNTHETIC-PARAGRAPH' not in str(selection)
            assert all(e.value is None and e.name is None for e in selection.elements)
            passed('selection_only_boundary')
        desktop.service.window_provider = lambda: target
        desktop.service.uia_provider = lambda *args, **kwargs: result
        desktop.permissions.configure(ui='ask', selection='ask')
        desktop.service.safe_mode = lambda: False
        snapshot = desktop.service.capture(once=('ui', 'selection'))
        desktop.preview()
        assert 'desktop_window' in desktop.panel.preview.toPlainText()
        assert 'DO-NOT-CAPTURE' not in desktop.panel.preview.toPlainText()
        report['measurements'].update(desktop.service.timings)
        passed('filtered_preview')
        from src.agent.ephemeral import DesktopAwareAgentStore
        store = DesktopAwareAgentStore(controller.paths.data_dir / 'phase15-temporary.sqlite3')
        session = AgentSession.new('Synthetic desktop mission')
        store.create_session(session)
        store.enable_temporary(session.session_id)
        step = AgentStep('SYNTHETIC-PRIVATE-TEXT', 1, 'Synthetic private title', '', 'read_context', 'context:desktop',
            input_bindings={'text': 'SYNTHETIC-PRIVATE-TEXT'}, risk_level='read_only', approval_mode='automatic_safe')
        plan = AgentPlan('phase15_plan', session.session_id, 1, 'SYNTHETIC-PRIVATE-TEXT', (step,))
        store.add_plan(plan)
        store.set_step(plan.plan_id, step.step_id, 'success', output={'content': 'SYNTHETIC-PRIVATE-TEXT'},
            evidence={'verified': True, 'method': 'schema'})
        assert store.step_states(plan.plan_id)[step.step_id]['output']['content'] == 'SYNTHETIC-PRIVATE-TEXT'
        with store.connection() as db:
            persisted = '\n'.join(str(tuple(row)) for table in ('plans', 'steps')
                for row in db.execute('SELECT * FROM ' + table))
        assert 'SYNTHETIC-PRIVATE-TEXT' not in persisted
        store.clear_temporary(session.session_id)
        assert 'SYNTHETIC-PRIVATE-TEXT' not in json.dumps(store.export_session(session.session_id))
        passed('agent_temporary_history')
        engine = ProactiveEngine(safe_mode=lambda: False)
        engine.start(enabled_sources=('metadata',))
        suggestions = engine.evaluate(snapshot, screenshot_skill='assignment', screenshot_id='explicit-fixture')
        assert suggestions and suggestions[0].goal == 'Organize this assignment.'
        assert not hasattr(suggestions[0], 'execute')
        passed('local_suggestion')
        desktop.stop()
        assert desktop.service.latest is None and not desktop.permissions.grants and not desktop.timer.isActive()
        passed('stop_cleanup')
        from src.reliability.policy import get_safety_policy
        policy = get_safety_policy()
        policy.set_safe_mode(True)
        try:
            assert not policy.allows('desktop_assistance') and not policy.allows('desktop_uia')
            try:
                desktop.start()
            except PermissionError:
                pass
            else:
                raise AssertionError('Safe Mode allowed session.')
        finally:
            policy.set_safe_mode(False)
        passed('safe_mode')
        assert set(report['gates']) == set(GATES)
        report['status'] = 'PASS'
        code = 0
    except Exception as error:
        report['error_type'] = type(error).__name__
        # Owned synthetic fixture only; no provider/desktop values in diagnostic text.
    finally:
        if widget:
            widget.close()
        controller.desktop_controller.stop()
        marker = controller.paths.data_dir / 'phase15_smoke.json'
        marker.write_text(json.dumps(report, indent=2), encoding='utf-8')
        controller.quit()
        app.exit(code)
