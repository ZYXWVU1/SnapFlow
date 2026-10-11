"""Guided sequential scheduling. Every effect has a durable, non-replayable claim."""
from threading import Event
import time
from src.observability.performance import emit, trace
from src.reliability.policy import get_safety_policy
from .approvals import AgentApprovalService
from .capabilities import AgentInvocationStopped
from .bindings import resolve_bindings
from .budget import inference_budget
from .models import thaw
from .validation import AgentPlanValidator, validate_inputs
from .verification import AgentVerifier


def _returned_outcome(output):
    if not isinstance(output, dict):
        return 'unknown'
    if output.get('success') is False:
        return 'failed'
    if output.get('success') is True or output.get('status') == 'success':
        return 'completed'
    resource_id = output.get('resource_id')
    if type(resource_id) in (str, int) and bool(resource_id):
        return 'completed'
    return 'unknown'


class AgentExecutor:
    def __init__(self, store, registry, journal, *, safety_policy=None,
                 private_provider=None, source_guard=None, source_ids=(), progress=None):
        self.store, self.registry, self.journal = store, registry, journal
        self.safety_policy = safety_policy or get_safety_policy()
        self.private_provider = private_provider or (lambda: False)
        self.source_guard = source_guard
        self.approvals = AgentApprovalService(store, registry)
        self.verifier = AgentVerifier(source_ids=source_ids, source_guard=source_guard)
        self.progress = progress

    def report(self, session_id):
        session = self.store.get_session(session_id)
        states = self.store.step_states(session.current_plan_id) if session.current_plan_id else {}
        warnings = [warning for state in states.values() for warning in (state['evidence'] or {}).get('warnings', ())]
        return {'session_id': session_id, 'status': session.status, 'goal': session.goal,
            'steps': states, 'warnings': list(dict.fromkeys(warnings)),
            'completed_effects': [s['id'] for s in states.values() if s['attempted'] and _returned_outcome(s['output']) == 'completed'],
            'uncertain_effects': [s['id'] for s in states.values() if s['attempted'] and _returned_outcome(s['output']) == 'unknown'],
            'result': next((s['output'].get('text') for s in reversed(list(states.values())) if s['status'] == 'success' and s['output'] and s['output'].get('text')), None)}

    def _status(self, session_id, status):
        self.store.update_session(session_id, status=status, active=status not in ('completed', 'failed', 'cancelled'))
        if self.progress:
            self.progress(self.report(session_id))

    def execute(self, session_id, *, cancel=None, pause=None, reviewed_resume=False):
        cancel, pause = cancel or Event(), pause or Event()
        session = self.store.get_session(session_id)
        if session.status in ('completed', 'cancelled'):
            return self.report(session_id)
        if session.status == 'interrupted' and not reviewed_resume:
            return self.report(session_id)
        if session.execution_mode == 'plan_only' or not session.current_plan_id:
            self._status(session_id, 'blocked')
            return self.report(session_id)
        if not self.store.acquire(session_id):
            raise ValueError('This mission is already executing.')
        started = time.monotonic()
        try:
            plan = self.store.get_plan(session.current_plan_id)
            try:
                self.safety_policy.require('agent_execution')
                AgentPlanValidator(self.registry).require(plan, session, private=self.private_provider(), **self.store.validation_context(plan))
                if self.source_guard:
                    self.source_guard()
            except (ValueError, PermissionError):
                self._status(session_id, 'blocked')
                return self.report(session_id)
            states = self.store.step_states(plan.plan_id)
            outputs = {sid: s['output'] for sid, s in states.items() if s['status'] == 'success'}
            self._status(session_id, 'running')
            for step in plan.steps:
                invocation = None
                output, journal_finished = None, False
                state = self.store.step_states(plan.plan_id)[step.step_id]
                if state['status'] == 'success':
                    continue
                if state['attempted'] or state['status'] == 'verification_failed':
                    self._status(session_id, 'blocked')
                    return self.report(session_id)
                if cancel.is_set():
                    self.store.set_step(plan.plan_id, step.step_id, 'cancelled')
                    self._status(session_id, 'cancelled')
                    return self.report(session_id)
                if pause.is_set():
                    self._status(session_id, 'paused')
                    return self.report(session_id)
                if session.elapsed_runtime_seconds + time.monotonic() - started > session.constraints.max_runtime_minutes * 60:
                    self._status(session_id, 'blocked')
                    return self.report(session_id)
                if not all(dep in outputs for dep in step.dependencies):
                    self.store.set_step(plan.plan_id, step.step_id, 'skipped' if step.optional else 'blocked')
                    if step.optional:
                        continue
                    self._status(session_id, 'blocked')
                    return self.report(session_id)
                try:
                    inputs = resolve_bindings(step.input_bindings, outputs)
                    capability = self.registry.get(step.capability_id)
                    validate_inputs(capability.input_schema, inputs)
                    if not self.approvals.permits(plan, session, step, inputs):
                        self.store.set_step(plan.plan_id, step.step_id, 'awaiting_approval')
                        self._status(session_id, 'awaiting_approval')
                        return self.report(session_id)
                    fingerprint = capability.fingerprint

                    def guard():
                        self.safety_policy.require('agent_execution')
                        current = self.store.get_session(session_id)
                        if current.elapsed_runtime_seconds + time.monotonic() - started > current.constraints.max_runtime_minutes * 60:
                            raise ValueError('Mission runtime budget exhausted.')
                        if current.status == 'cancelled' or cancel.is_set():
                            raise ValueError('Mission cancelled.')
                        AgentPlanValidator(self.registry).require(plan, current, private=self.private_provider(), **self.store.validation_context(plan))
                        fresh = self.registry.get(step.capability_id)
                        if fresh is None or fresh.fingerprint != fingerprint or not self.approvals.permits(plan, current, step, inputs):
                            raise ValueError('Policy or reviewed capability changed.')
                        if self.source_guard:
                            self.source_guard()

                    guard()
                    self.store.charge(session_id, writes=1 if capability.risk_level == 'external_write' else 0)
                    self.store.claim_step(plan.plan_id, step.step_id, side_effecting=capability.side_effecting)
                    invocation = None
                    if capability.side_effecting:
                        self.safety_policy.require('external_write' if capability.risk_level == 'external_write' else 'agent_execution')
                        invocation = self.journal.plan('integration_write' if capability.risk_level == 'external_write' else 'local_write')
                        self.store.set_step(plan.plan_id, step.step_id, 'running', invocation=invocation)
                        self.journal.approved(invocation)
                        guard()
                        self.journal.started(invocation)
                    emit('agent_step_started', component='agent', properties={'risk': capability.risk_level})
                    with trace('agent_execution', 'agent'):
                        with inference_budget(getattr(self.registry, 'inference_client', None),
                                lambda: self.store.charge(session_id, ai_calls=1), fallback_calls=capability.ai_calls):
                            output = self.registry.invoke(step.capability_id, inputs, cancelled=cancel.is_set,
                                guard=guard, constraints=session.constraints.effective(private=self.private_provider()))
                    if invocation:
                        self.journal.finish(invocation, _returned_outcome(output))
                        journal_finished = True
                    with trace('agent_verification', 'agent'):
                        evidence = self.verifier.verify(step, capability, output)
                    status = 'success' if evidence.verified else 'verification_failed'
                    self.store.set_step(plan.plan_id, step.step_id, status, output=output, evidence=evidence.to_dict())
                    emit('agent_step_completed' if evidence.verified else 'agent_step_failed', component='agent', success=evidence.verified,
                         properties={'risk': capability.risk_level})
                    if self.progress:
                        self.progress(self.report(session_id))
                    if not evidence.verified:
                        if step.optional:
                            continue
                        self._status(session_id, 'blocked')
                        return self.report(session_id)
                    outputs[step.step_id] = output
                except Exception as error:
                    returned_output = error.output if isinstance(error, AgentInvocationStopped) else output
                    if returned_output is not None:
                        if invocation and not journal_finished:
                            try:
                                self.journal.finish(invocation, _returned_outcome(returned_output))
                            except Exception:
                                pass
                        receipt = []
                        if isinstance(returned_output, dict):
                            if 'success' in returned_output:
                                receipt.append({'type': 'reported_success', 'value': returned_output['success']})
                            if 'resource_id' in returned_output:
                                receipt.append({'type': 'returned_identifier', 'field': 'resource_id', 'value': returned_output['resource_id']})
                        self.store.set_step(plan.plan_id, step.step_id, 'verification_failed', output=returned_output,
                            evidence={'verified': False, 'method': 'returned_receipt', 'evidence': receipt,
                                'warnings': ['Execution stopped after a result was returned. The receipt was preserved; review the effect and do not replay the write.']})
                        self._status(session_id, 'cancelled' if cancel.is_set() else 'blocked')
                        return self.report(session_id)
                    # Write failure/timeout is ambiguous; never infer that nothing happened.
                    if invocation:
                        try:
                            self.journal.finish(invocation, 'unknown')
                        except Exception:
                            pass
                    state = self.store.step_states(plan.plan_id)[step.step_id]
                    status = 'interrupted' if state['attempted'] else 'cancelled' if cancel.is_set() else 'failed'
                    self.store.set_step(plan.plan_id, step.step_id, status,
                        evidence={'verified': False, 'warnings': ['Operation stopped. Review current permissions, sources and any possible completed effects.']})
                    self._status(session_id, 'cancelled' if cancel.is_set() else 'interrupted' if state['attempted'] else 'blocked')
                    return self.report(session_id)
            states = self.store.step_states(plan.plan_id)
            complete = all(states[s.step_id]['status'] == 'success' or s.optional and states[s.step_id]['status'] == 'skipped' for s in plan.steps)
            self._status(session_id, 'cancelled' if cancel.is_set() else 'completed' if complete else 'blocked')
            if complete:
                emit('agent_session_completed', component='agent', success=True)
            return self.report(session_id)
        finally:
            try:
                self.store.charge_runtime(session_id, time.monotonic() - started)
            finally:
                self.store.release(session_id)
