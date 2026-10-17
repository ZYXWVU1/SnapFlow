"""Explicit disposable-profile acceptance. Never enabled by ordinary application UI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def install_smoke_isolation():
    profile = Path(os.environ.get('SNAPFLOW_PHASE17_PROFILE', '')).absolute()
    if (os.environ.get('SNAPFLOW_PHASE17_SMOKE') != '1' or
            profile != Path(os.environ.get('APPDATA', '')).absolute() or
            profile != Path(os.environ.get('LOCALAPPDATA', '')).absolute() or
            not (profile / '.snapflow-phase17-test').is_file()):
        raise ValueError('Phase 17 smoke requires a disposable profile.')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in tuple(os.environ):
        if key.upper().startswith(('AI_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_', 'GOOGLE_API_', 'GEMINI_', 'MISTRAL_')):
            os.environ.pop(key, None)
    def audit(event, args):
        if event == 'socket.connect':
            raise PermissionError('Phase 17 acceptance denies external sockets.')
    sys.addaudithook(audit)
    from src.integrations import credentials
    class EmptyBackend:
        def get(self, target):
            return None
        def set(self, *args):
            raise PermissionError('No production credentials in a disposable smoke profile.')
        delete = set
    credentials.WindowsCredentialBackend = EmptyBackend


def run_packaged_automation_smoke(app, owner):
    report = {'status': 'FAIL', 'gates': {}, 'external_sockets': 'denied', 'synthetic_content_only': True}
    code = 1
    try:
        if os.environ.get('SNAPFLOW_PHASE17_GATES') == 'backup':
            from src.backup import BackupService
            with tempfile.TemporaryDirectory(prefix='snapflow-phase17-backup-') as directory:
                backup = Path(directory) / 'automation.zip'
                BackupService(owner.paths).export_backup(backup)
                assert 'automation' in BackupService(owner.paths).inspect_backup(backup).categories
            report['gates']['actual_app_backup_includes_definitions'] = 'PASS'
            report['status'] = 'PASS'
            code = 0
            return
        automation = owner.automation_controller
        assert not automation.enabled() and automation.store is None and automation.teach_dialog is None
        assert not owner.paths.automation_skills_file.exists()
        report['gates']['actual_app_lazy_default_off'] = 'PASS'
        owner.main_window.open_page('skills')
        assert hasattr(owner.main_window.pages['skills'], 'automation_requested')
        automation.show()
        assert automation.library and automation.library.isVisible()
        from src.desktop_actions.synthetic import SyntheticDesktopProvider
        from .models import AutomationSkill, AutomationStepTemplate, TargetTemplate
        from .storage import AutomationSkillStore
        provider = SyntheticDesktopProvider()
        element = next(e for e in provider.elements if e.element_id == 'editor')
        skill = AutomationSkill('Fabricated Project Name', (AutomationStepTemplate('name', 'Project Name', 'set_value',
            TargetTemplate.from_element(element, 'set_value'), '${input.project_name}', risk_level='ui_local_edit'),),
            inputs=())
        from .models import AutomationInput
        from dataclasses import replace
        skill = replace(skill, inputs=(AutomationInput('project_name', 'Project Name'),))
        automation.store.save_draft(skill)
        from src.ui.automation.editor import AutomationEditor
        from src.ui.automation.run import AutomationRunDialog
        from src.ui.automation.teach import TeachDialog
        editor = AutomationEditor(automation, skill)
        editor.show()
        app.processEvents()
        assert editor.tabs.count() == 7 and editor.steps.count() == 1 and editor.inputs.count() == 1
        editor.grab().save(str(owner.paths.data_dir / 'phase17-editor.png'))
        editor.close()
        runner = AutomationRunDialog(automation, skill, testing=True)
        assert not runner.approve.isEnabled() and not runner.review.isEnabled()
        runner.close()
        teach = TeachDialog(owner, enabled=lambda: False)
        teach.show()
        app.processEvents()
        assert teach.isVisible() and teach.recorder.session is None and not teach.timer.isActive()
        teach.close()
        report['gates']['library_editor_run_and_visible_teach_forms'] = 'PASS'
        from src.backup import BackupService
        with tempfile.TemporaryDirectory(prefix='snapflow-phase17-backup-') as directory:
            backup = Path(directory) / 'automation.zip'
            BackupService(owner.paths).export_backup(backup)
            assert 'automation' in BackupService(owner.paths).inspect_backup(backup).categories
        report['gates']['actual_app_backup_includes_definitions'] = 'PASS'
        if getattr(sys, 'frozen', False):
            _native_procedure(app, owner, report)
        report['status'] = 'PASS'
        code = 0
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error'] = str(exc)[:500]  # Only owned fabricated smoke assertions reach here.
    finally:
        owner.paths.data_dir.mkdir(parents=True, exist_ok=True)
        (owner.paths.data_dir / 'phase17-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
        owner.quit()
        if code:
            app.exit(code)


def _native_procedure(app, owner, report):
    """New frozen slice: native text checkpoint, variable reuse and external Submit."""
    from dataclasses import replace
    from threading import Thread
    import ctypes
    from ctypes import wintypes
    from src.desktop_actions.synthetic_app import SyntheticActionWindow
    from src.desktop_actions.models import DesktopActionFence
    from src.desktop_actions.native import WindowsDesktopActionProvider
    from src.desktop_actions.service import DesktopActionService
    from src.reliability.journal import ExecutionJournal
    from .recorder import DemonstrationRecorder
    from .observer import WindowsDemonstrationObserver
    from .compiler import DemonstrationInterpreter
    from .parameters import parameterize
    from .models import ConditionTemplate, TargetTemplate, AutomationStepTemplate
    from .runtime import AutomationRuntime
    from .storage import AutomationSkillStore
    owner.automation_controller.library.hide()
    owner.main_window.hide()
    fixture = SyntheticActionWindow()
    fixture.show()
    fixture.activateWindow()
    app.processEvents()
    fence = DesktopActionFence(os.getpid(), Path(sys.executable).name, int(fixture.winId()), 'phase17_frozen')
    recorder = DemonstrationRecorder(enabled=lambda: True)
    observer = WindowsDemonstrationObserver(recorder)
    recorder.start('Enter Project and Submit', (fence,))
    flags, errors = {'ready': False, 'typed': False, 'reset': False, 'reset_done': False}, []
    def worker():
        try:
            observer.start((fence,))
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                observer.drain()
                if observer.ready:
                    flags['ready'] = True
                if flags['typed']:
                    observer.checkpoint()
                    observer.drain()
                    break
                time.sleep(.05)
            assert flags['typed'], 'Native observation did not become ready.'
            session = recorder.finish()
            observer.close()
            skill = DemonstrationInterpreter().compile(session)
            assert len(skill.steps) == 1 and skill.steps[0].action_type == 'set_value', 'Text state inference did not produce one step.'
            skill = parameterize(skill, skill.steps[0].step_id, 'project_name', 'Project Name')
            assert 'SnapFlow Example' not in str(skill.to_dict())
            flags['reset'] = True
            while not flags['reset_done']:
                time.sleep(.01)
            provider = WindowsDesktopActionProvider()
            state = provider.capture(fence)
            submit = next(e for e in state.elements if e.automation_id.endswith('.submit_form'))
            status = next(e for e in state.elements if e.automation_id.endswith('.submission_status'))
            outcome = replace(TargetTemplate.from_element(status, 'invoke'), name_template=None, expected_actions=(), expected_patterns=())
            commit = AutomationStepTemplate('submit', 'Submit Synthetic Form', 'invoke', TargetTemplate.from_element(submit, 'invoke'),
                postconditions=(ConditionTemplate(outcome, 'name', 'Submitted SF-0001'),),
                dependencies=(skill.steps[0].step_id,), risk_level='ui_external_commit', reviewed=True)
            skill = replace(skill, steps=(*tuple(replace(s, reviewed=True) for s in skill.steps), commit))
            journal = ExecutionJournal(owner.paths.data_dir / 'phase17-owned-journal.db')
            service = DesktopActionService(provider, journal, enabled=lambda: True, minimum_interval=0,
                local_processes=(Path(sys.executable).name,), allow_owned_test_window=True)
            service.activate_fence(fence)
            runtime = AutomationRuntime(service, enabled=lambda: True)
            run = runtime.prepare(skill, {'project_name': 'Phase17'})
            previews = runtime.dry_run(run)
            assert all(p.allowed for p in previews)
            assert previews[0].resolution.resolved_element.state.get('value') == ''
            assert previews[1].risk_level == 'ui_external_commit'
            for _ in range(2):
                preview = runtime.preview(run)
                receipt = runtime.execute_next(run, service.approve(preview))  # Fabricated owned harness stands in for two explicit user approvals.
                assert receipt.status == 'success', receipt.status
            assert run.status == 'completed'
            store = AutomationSkillStore(owner.paths.data_dir / 'phase17-owned-skills.json')
            store.save_draft(skill)
            store.review(skill.version_id)
            store.record_test(run, previews)
            published = store.publish(skill.version_id)
            assert published.status == 'published'
            from src.evaluation.automation.metrics import evaluate_run
            report['evaluation'] = evaluate_run(run, previews, expected_actions=('set_value', 'invoke'), expected_inputs=('project_name',))
            report['gates']['frozen_native_checkpoint_text_parameter_and_moved_layout'] = 'PASS'
            report['gates']['frozen_native_external_commit_two_exact_approvals_and_verified_publish'] = 'PASS'
            report['native_step_count'] = len(run.results)
            report['native_events_received'] = observer.event_count
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
        finally:
            observer.close()
    thread = Thread(target=worker)
    started = time.perf_counter()
    thread.start()
    ticks = 0
    ctypes.windll.user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    while thread.is_alive() and time.perf_counter() - started < 100:
        app.processEvents()
        ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(int(fixture.winId())))
        if flags['ready'] and not flags['typed'] and WindowsDesktopActionProvider().foreground_matches(fence):
            fixture.editor.setText('SnapFlow Example')
            flags['typed'] = True
        if flags['reset'] and not flags['reset_done']:
            fixture.editor.clear()
            fixture.move(fixture.x() + 70, fixture.y() + 30)
            flags['reset_done'] = True
        ticks += 1
        time.sleep(.005)
    thread.join(timeout=2)
    report['native_duration_seconds'] = round(time.perf_counter() - started, 3)
    report['responsive_ticks'] = ticks
    fixture.close()
    if errors or thread.is_alive():
        raise AssertionError('; '.join(errors) or 'Native worker did not finish.')
