"""Explicitly gated, disposable Qt Agent acceptance with synthetic inference."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

SMOKE_GATES = ('beta_default_off', 'safe_mode_denied', 'qt_page', 'current_result',
    'selected_memory', 'planner', 'plan_review', 'read_only_execution', 'verification',
    'worker_lifecycle', 'recovery_no_replay')


def install_smoke_isolation():
    """Run before application startup; this switch never uses a real profile."""
    profile = Path(os.environ.get('SNAPFLOW_PHASE14_PROFILE', '')).absolute()
    if (os.environ.get('SNAPFLOW_PHASE14_SMOKE') != '1' or
            profile != Path(os.environ.get('APPDATA', '')).absolute() or
            profile != Path(os.environ.get('LOCALAPPDATA', '')).absolute() or
            not (profile / '.snapflow-phase14-test').is_file()):
        raise ValueError('Missing disposable Agent smoke profile.')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for key in tuple(os.environ):
        upper = key.upper()
        if upper.startswith(('AI_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_', 'GOOGLE_API_', 'GEMINI_', 'MISTRAL_')):
            os.environ.pop(key, None)
    def deny_sockets(event, args):
        if event == 'socket.connect':
            raise PermissionError('Agent smoke denies all socket connections.')
    sys.addaudithook(deny_sockets)
    from src.integrations import credentials
    class EmptyCredentialBackend:
        def get(self, target):
            return None
        def set(self, target, secret):
            raise PermissionError('Agent smoke cannot persist credentials.')
        def delete(self, target):
            raise PermissionError('Agent smoke cannot change credentials.')
    credentials.WindowsCredentialBackend = EmptyCredentialBackend


def check_interruption_no_replay(paths):
    """A claimed synthetic effect survives restart and cannot be dispatched."""
    from .models import AgentSession, AgentPlan, AgentStep, AgentConstraints
    from .storage import AgentStore
    from .capabilities import AgentCapability, AgentCapabilityRegistry
    from .executor import AgentExecutor
    from src.reliability.journal import ExecutionJournal
    from src.reliability.policy import SafetyPolicy
    store = AgentStore(paths.data_dir / 'phase14-recovery.sqlite3')
    session = AgentSession('smoke_recovery', 'Synthetic interrupted effect',
        constraints=AgentConstraints(allow_external_writes=True))
    store.create_session(session)
    step = AgentStep('effect', 1, 'Synthetic effect', 'Never dispatch this claimed effect',
        'call_action', 'synthetic_effect', risk_level='external_write', approval_mode='approve_at_execution')
    plan = AgentPlan('smoke_recovery_plan', session.session_id, 1, session.goal, (step,))
    store.add_plan(plan)
    store.grant(plan.plan_id, step.step_id, 'synthetic-review-grant')
    store.acquire(session.session_id)
    store.claim_step(plan.plan_id, step.step_id, side_effecting=True)
    store.update_session(session.session_id, status='running')
    reopened = AgentStore(store.path)
    if reopened.recover_interrupted() != 1:
        raise RuntimeError('Interrupted mission was not recovered.')
    state = reopened.step_states(plan.plan_id)[step.step_id]
    if (reopened.get_session(session.session_id).status != 'interrupted' or state['attempted'] != 1 or
            state['status'] != 'interrupted' or reopened.approval(plan.plan_id, step.step_id) is not None):
        raise RuntimeError('Recovered mission lost its non-replayable receipt.')
    calls = []
    registry = AgentCapabilityRegistry()
    registry.register(AgentCapability('synthetic_effect', 'Synthetic effect', 'In-process fixture',
        'call_action', {'type': 'object'}, {'type': 'object'}, 'external_write', True, True, 'synthetic'),
        lambda inputs, **kwargs: calls.append(inputs) or {'resource_id': 'never'})
    executor = AgentExecutor(reopened, registry, ExecutionJournal(paths.data_dir / 'phase14-recovery-journal.sqlite3'),
                             safety_policy=SafetyPolicy())
    if executor.execute(session.session_id)['status'] != 'interrupted' or calls:
        raise RuntimeError('Interrupted mission resumed automatically.')
    reopened.acquire(session.session_id)
    try:
        reopened.claim_step(plan.plan_id, step.step_id, side_effecting=True)
    except ValueError:
        pass
    else:
        raise RuntimeError('Attempted effect was replayable.')
    finally:
        reopened.release(session.session_id)
    return True


def _wait_idle(agent, *, timeout_ms=20000):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: loop.quit() if not agent.busy else None)
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout_ms)
    if agent.busy:
        loop.exec()
    timer.stop()
    deadline.stop()
    if agent.busy:
        raise RuntimeError('Agent worker did not complete.')


def run_packaged_agent_smoke(app, controller):
    """Use the real page/controller workers and mission service; no live AI."""
    code = 1
    report = {'schema_version': 1, 'status': 'FAIL', 'synthetic_ai': True,
              'live_inference': 'NOT VERIFIED', 'external_sockets': 'denied', 'gates': {}}
    policy = controller.beta_features.policy
    try:
        from .mission import AgentMissionService
        from .services import AgentServices
        from .storage import AgentStore
        from src.modes import ModeResult
        agent = controller.agent_controller
        page = agent.page
        if controller.beta_features.configured('agent_missions_v1') or page.create_plan_button.isEnabled():
            raise RuntimeError('Agent beta was not off by default.')
        report['gates']['beta_default_off'] = 'PASS'
        if not policy.safe_mode:
            raise RuntimeError('Smoke launch did not start in Safe Mode.')
        for operation in ('agent_execution', 'agent_resume', 'external_write'):
            try:
                policy.require(operation)
            except PermissionError:
                continue
            raise RuntimeError('Safe Mode allowed Agent dispatch.')
        report['gates']['safe_mode_denied'] = 'PASS'
        # Explicit test-only transition, within the marked isolated profile.
        policy.set_safe_mode(False)
        controller.config = replace(controller.config, private_mode=True, ai_execution_mode='local_only',
                                    local_ai_model='synthetic-smoke-model', visual_memory_enabled=True)
        controller.beta_features.save({'agent_missions_v1': True}, experiments_enabled=False)
        selected = controller.memory_store.save_result(ModeResult('manual_note', '',
            {'note': 'Synthetic saved deadline is 2026-10-05.'}), title='Synthetic selected source')
        controller.memory_store.save_result(ModeResult('manual_note', '',
            {'note': 'UNSELECTED_SYNTHETIC_SENTINEL'}), title='Unselected synthetic source')
        controller.last_result = ModeResult('manual_note', 'Synthetic current analysis',
                                           {'note': 'Synthetic current deadline is 2026-10-12.'})
        controller.capture_created_at = 'synthetic-phase14-capture'
        client = _SyntheticClient()
        agent.store = AgentStore(controller.paths.agent_database)
        agent.services = AgentServices(controller.memory_store, client,
            current_result=controller._current_context_data,
            screenshot_reference=lambda: controller.capture_created_at,
            config_provider=lambda: controller.config)
        agent.service = AgentMissionService(agent.store, agent.services, controller.execution_journal,
            safety_policy=policy, progress=agent.progress_changed.emit)
        agent.service.additional_guard = agent._guard
        agent.refresh_enabled()
        controller.main_window.open_page('agent')
        page.show()
        report['gates']['qt_page'] = 'PASS'
        page.goal.setText('Compare the current analysis with the selected synthetic saved deadline')
        page.include_current.setChecked(True)
        page.refresh_memories()
        page.select_memory(selected.id)
        if page.selected_memory_ids() != [selected.id]:
            raise RuntimeError('Selected Memory page scope differs.')
        page.create_plan_button.click()
        _wait_idle(agent)
        if not agent.session_id:
            raise RuntimeError('Page planning did not create a mission.')
        session = agent.store.get_session(agent.session_id)
        plan = agent.store.get_plan(session.current_plan_id)
        if len(plan.steps) != 3 or session.status != 'awaiting_approval':
            raise RuntimeError('Structured planning failed.')
        report['gates']['planner'] = 'PASS'
        if page.reviewed.isChecked() or page.execute_button.isEnabled() or client.comparisons:
            raise RuntimeError('Unreviewed plan could execute.')
        page.reviewed.setChecked(True)
        page.approve_safe_button.click()
        if client.comparisons or not page.execute_button.isEnabled():
            raise RuntimeError('Approval executed instead of waiting for dispatch.')
        report['gates']['plan_review'] = 'PASS'
        page.execute_button.click()
        _wait_idle(agent)
        result = agent.service.execute(agent.session_id)
        if result['status'] != 'completed' or client.comparisons != 1:
            raise RuntimeError('Reviewed plan did not execute exactly once.')
        states = agent.store.step_states(plan.plan_id)
        if not all(state['status'] == 'success' and state['evidence']['verified'] for state in states.values()):
            raise RuntimeError('Mission lacked verified step evidence.')
        if not result['result'] or '[C1]' not in result['result'] or '[M1]' not in result['result']:
            raise RuntimeError('Final report lacks selected-source evidence.')
        if ('completed' not in page.report.toPlainText() or not page.evidence.toPlainText() or
                'UNSELECTED_SYNTHETIC_SENTINEL' in '\n'.join(client.prompts)):
            raise RuntimeError('Page evidence or selected-source privacy failed.')
        report['gates'].update(current_result='PASS', selected_memory='PASS',
            read_only_execution='PASS', verification='PASS', worker_lifecycle='PASS')
        check_interruption_no_replay(controller.paths)
        report['gates']['recovery_no_replay'] = 'PASS'
        if set(report['gates']) != set(SMOKE_GATES):
            raise RuntimeError('A required smoke gate is absent.')
        report.update(status='PASS', step_count=3, synthetic_comparisons=client.comparisons)
        code = 0
    except Exception as error:
        report['error_type'] = type(error).__name__
        # Messages below originate only from this synthetic harness.
        print('Phase 14 smoke failed: ' + type(error).__name__ + ': ' + str(error), flush=True)
    finally:
        policy.set_safe_mode(True)
        controller.paths.data_dir.mkdir(parents=True, exist_ok=True)
        controller.paths.data_dir.joinpath('phase14_smoke.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        controller.quit()
        if code:
            app.exit(code)


class _SyntheticClient:
    policy = SimpleNamespace(private_mode=True)

    def __init__(self):
        self.prompts = []
        self.comparisons = 0

    def request_text(self, prompt, **kwargs):
        if kwargs.get('cancelled') and kwargs['cancelled']():
            raise ValueError('Synthetic request cancelled.')
        if kwargs.get('privacy_requirement') != 'local_only':
            raise ValueError('Synthetic smoke expected local-only dispatch.')
        self.prompts.append(prompt)
        if kwargs.get('mode') == 'agent_planning':
            raw = json.dumps({'goal_summary': 'Compare selected synthetic deadlines', 'steps': [
                {'step_id': 'current', 'title': 'Read current analysis', 'step_type': 'read_context', 'capability_id': 'context:current'},
                {'step_id': 'memory', 'title': 'Read selected Memory', 'step_type': 'retrieve_memory', 'capability_id': 'memory:selected'},
                {'step_id': 'compare', 'title': 'Compare selected sources', 'step_type': 'compare_data', 'capability_id': 'ai:compare',
                 'input_bindings': {'current': '${current.content}', 'memory': '${memory.content}'},
                 'dependencies': ['current', 'memory'], 'verification': {'method': 'source_grounding'}}]})
            if kwargs.get('validator'):
                kwargs['validator'](raw)
            return raw
        self.comparisons += 1
        return 'The current deadline is 2026-10-12 [C1]. The selected saved deadline is 2026-10-05 [M1].'
