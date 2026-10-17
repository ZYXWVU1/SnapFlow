"""Goal intake, reviewed plan versions and explicit mission operations."""
from dataclasses import replace
import json
import time
from threading import Event
from uuid import uuid4
from src.observability.performance import emit, trace
from src.reliability.policy import get_safety_policy
from .approvals import AgentApprovalService
from .bindings import resolve_bindings
from .budget import inference_budget
from .context import assemble_agent_context
from .executor import AgentExecutor
from .intake import constrain_goal
from .models import AgentConstraints, AgentSession, AgentPlan, digest, thaw
from .planner import AgentPlanner
from .validation import AgentPlanValidator


class AgentMissionService:
    def __init__(self, store, services, journal, *, safety_policy=None, progress=None):
        self.store, self.services, self.journal = store, services, journal
        self.safety_policy = safety_policy or get_safety_policy()
        self.progress = progress
        self.contexts = {}
        self.controls = {}
        self.running_since = {}

    def _private(self):
        config = self.services.config_provider()
        return bool(getattr(config, 'private_mode', False) or getattr(getattr(self.services.client, 'policy', None), 'private_mode', False))

    @staticmethod
    def _signature(bundle):
        return [{'source_id': s.source_id, 'type': s.source_type, 'memory_id': s.memory_id,
            'revision': s.memory_revision, 'capture': s.screenshot_reference, 'hash': digest(s.content)} for s in bundle.sources]

    def create(self, payload, *, cancelled=None, conversation=(), external_sources=(), desktop_sources=()):
        self.safety_policy.require('agent_execution')
        if type(payload) is not dict or type(payload.get('goal')) is not str:
            raise ValueError('Enter a goal to create a mission.')
        constraints = constrain_goal(payload['goal'], AgentConstraints.from_dict(payload.get('constraints', {}))).effective(private=self._private())
        if constraints.max_estimated_cost is not None and constraints.allow_remote_ai and not self._private():
            raise ValueError('Cloud planning cost is unavailable; use Local AI for a strict cost limit.')
        selected = tuple(payload.get('selected_memory_ids', ()))
        include_current = payload.get('include_current', True)
        if type(include_current) is not bool:
            raise ValueError('Current context selection must be explicit.')
        if selected and constraints.allow_memory_search:
            raise ValueError('Choose selected memories or authorized search.')
        scope = 'selected_memories' if selected else 'authorized_memory_search' if constraints.allow_memory_search else 'current_only'
        session = AgentSession.new(payload['goal'], constraints=constraints, context_scope=scope,
            execution_mode=payload.get('execution_mode', payload.get('mode', 'guided')),
            user_constraints=tuple(payload.get('user_constraints', ())),
            privacy_mode='private' if self._private() else 'local_only' if not constraints.allow_remote_ai else 'standard')
        if desktop_sources and not callable(getattr(self.store, 'enable_temporary', None)):
            raise ValueError('Desktop missions require temporary plan/output storage.')
        self.store.create_session(session)
        if desktop_sources:
            self.store.enable_temporary(session.session_id)
        self.controls[session.session_id] = (Event(), Event())
        try:
            with trace('agent_context', 'agent'):
                bundle = assemble_agent_context(self.services.memory_store, self.services.current_result(),
                    self.services.screenshot_reference(), session.goal, selected_memory_ids=selected,
                    allow_search=constraints.allow_memory_search, conversation_history=conversation,
                    external_sources=external_sources, include_current=include_current,
                    desktop_sources=desktop_sources, desktop_validator=self.services.desktop_validator)
            # Clear/Stop releases temporary sessions under this same lock, including
            # missions still assembling. Never publish a bundle after that release.
            with self.store.lock:
                if desktop_sources:
                    if session.session_id in self.store.released_sessions:
                        raise ValueError('Desktop Context was cleared during mission setup.')
                    self.services.desktop_validator(desktop_sources)
                if self.store.get_session(session.session_id).status == 'cancelled':
                    raise ValueError('Mission cancelled during setup.')
                self.store.save_context(session.session_id, {'selected_memory_ids': list(selected),
                    'include_current': include_current, 'signature': self._signature(bundle),
                    'conversation_included': bool(conversation), 'external_included': bool(external_sources),
                    'desktop_included': bool(desktop_sources)})
                self.contexts[session.session_id] = bundle
                self.store.update_session(session.session_id, status='planning')
            self.running_since[session.session_id] = time.monotonic()
            if self.progress:
                self.progress({'session_id': session.session_id, 'status': 'planning'})
            emit('agent_session_started', component='agent')
            registry = self.services.registry(session, bundle)
            self._guard(session.session_id, bundle)
            dispatch = self._planning_dispatch(session.session_id, bundle, cancelled)
            with inference_budget(self.services.client, lambda: self._charge_inference(session.session_id, dispatch)):
                plan = AgentPlanner(self.services.client, registry, validation_context=self.store.validation_context).create_plan(
                    session, bundle.prompt, cancelled=dispatch)
            dispatch()
            self._guard(session.session_id, bundle)
            self.store.add_plan(plan)
        except Exception:
            if desktop_sources:
                with self.store.lock:
                    self.contexts.pop(session.session_id, None)
                    self.store.clear_temporary(session.session_id)
            if self.store.get_session(session.session_id).status != 'cancelled':
                self.store.update_session(session.session_id, status='failed')
            raise
        finally:
            started = self.running_since.pop(session.session_id, None)
            if started is not None:
                self.store.charge_runtime(session.session_id, time.monotonic() - started)
        return self.store.get_session(session.session_id)

    def _guard(self, session_id, bundle):
        self.safety_policy.require('agent_execution')
        if getattr(self, 'additional_guard', None):
            self.additional_guard(session_id)
        session = self.store.get_session(session_id)
        elapsed = session.elapsed_runtime_seconds
        if session_id in self.running_since:
            elapsed += time.monotonic() - self.running_since[session_id]
        if elapsed >= session.constraints.max_runtime_minutes * 60:
            raise ValueError('Mission runtime budget exhausted.')
        self.services.validate_sources(bundle)
        if self._signature(bundle) != self.store.context(session_id)['signature']:
            raise ValueError('Mission sources changed. Create a new plan with reviewed sources.')
        if self._private() and session.privacy_mode == 'standard':
            raise ValueError('Private Mode changed. Create a new local-only plan.')
        if session.status == 'cancelled':
            raise ValueError('Mission cancelled.')

    def _planning_dispatch(self, session_id, bundle, cancelled):
        def dispatch():
            self._guard(session_id, bundle)
            if (cancelled and cancelled()) or self.controls[session_id][0].is_set():
                raise ValueError('Mission planning cancelled.')
            return False
        return dispatch

    def _charge_inference(self, session_id, dispatch):
        dispatch()
        self.store.charge(session_id, ai_calls=1)

    def _context(self, session):
        if session.session_id not in self.contexts:
            metadata = self.store.context(session.session_id)
            if metadata['conversation_included'] or metadata['external_included'] or metadata.get('desktop_included'):
                raise ValueError('Reselect conversation or external context before creating a new plan.')
            bundle = assemble_agent_context(self.services.memory_store, self.services.current_result(),
                self.services.screenshot_reference(), session.goal,
                selected_memory_ids=metadata['selected_memory_ids'],
                allow_search=session.constraints.allow_memory_search,
                include_current=metadata['include_current'])
            if self._signature(bundle) != metadata['signature']:
                raise ValueError('Stored source references are stale. Review and create a new plan.')
            self.contexts[session.session_id] = bundle
        bundle = self.contexts[session.session_id]
        self._guard(session.session_id, bundle)
        return bundle

    def runtime(self, session_id):
        session = self.store.get_session(session_id)
        bundle = self._context(session)
        registry = self.services.registry(session, bundle)
        return session, self.store.get_plan(session.current_plan_id), registry, bundle

    def approve_safe(self, session_id):
        session, plan, registry, _ = self.runtime(session_id)
        AgentApprovalService(self.store, registry).approve_safe(plan, session)

    def resolved_inputs(self, session_id, step_id):
        session, plan, registry, _ = self.runtime(session_id)
        step = next((s for s in plan.steps if s.step_id == step_id), None)
        if step is None:
            raise ValueError('Select an existing step.')
        outputs = {sid: s['output'] for sid, s in self.store.step_states(plan.plan_id).items() if s['status'] == 'success'}
        return step, resolve_bindings(step.input_bindings, outputs)

    def approve_step(self, session_id, step_id, inputs):
        session, plan, registry, _ = self.runtime(session_id)
        step, current_inputs = self.resolved_inputs(session_id, step_id)
        if inputs != current_inputs:
            raise ValueError('Step arguments changed during review.')
        AgentApprovalService(self.store, registry).approve_step(plan, session, step_id, inputs)

    def execute(self, session_id, *, resume=False):
        session, plan, registry, bundle = self.runtime(session_id)
        cancel, pause = self.controls.setdefault(session_id, (Event(), Event()))
        if resume:
            self.safety_policy.require('agent_resume')
            self.store.revoke(plan.plan_id)
            # Resume requires fresh review grants; it never means approve all writes.
            self.store.update_session(session_id, status='awaiting_approval')
            cancel.clear()
            pause.clear()
        executor = AgentExecutor(self.store, registry, self.journal,
            safety_policy=self.safety_policy, private_provider=self._private,
            source_guard=lambda: self._guard(session_id, bundle), source_ids=[s.source_id for s in bundle.sources],
            progress=self.progress)
        return executor.execute(session_id, cancel=cancel, pause=pause, reviewed_resume=resume)

    def pause(self, session_id):
        self.controls.setdefault(session_id, (Event(), Event()))[1].set()

    def cancel(self, session_id):
        self.controls.setdefault(session_id, (Event(), Event()))[0].set()
        self.store.update_session(session_id, status='cancelled', active=False)
        session = self.store.get_session(session_id)
        if session.current_plan_id:
            self.store.revoke(session.current_plan_id)

    def _check_revision(self, old, new, registry):
        states = self.store.step_states(old.plan_id)
        old_steps = {s.step_id: s for s in old.steps}
        new_steps = {s.step_id: s for s in new.steps}
        attempted_capabilities = {old_steps[sid].capability_id for sid, s in states.items() if s['attempted']}
        for sid, state in states.items():
            if state['status'] == 'success' or state['attempted']:
                if sid not in new_steps or new_steps[sid].to_dict() != old_steps[sid].to_dict():
                    raise ValueError('Keep completed and attempted steps unchanged; their effects are historical facts.')
        for step in new.steps:
            if step.step_id not in old_steps and step.capability_id in attempted_capabilities:
                raise ValueError('A revised plan cannot silently repeat an attempted write capability.')

    def edit(self, session_id, text):
        session, old, registry, _ = self.runtime(session_id)
        if type(text) is not str or len(text.encode('utf-8')) > 262144:
            raise ValueError('Edited plan is oversized.')
        raw = json.loads(text)
        raw.update(plan_id=uuid4().hex, session_id=session_id, version=old.version + 1, supersedes_plan_id=old.plan_id)
        new = AgentPlan.from_dict(raw)
        AgentPlanValidator(registry).require(new, session, private=self._private(), consumed=self.store.budget(session_id),
            step_states=self.store.step_states(old.plan_id))
        self._check_revision(old, new, registry)
        self.store.add_plan(new)
        self.store.carry_completed(old, new)
        return new

    def replan(self, session_id, *, cancelled=None):
        session, old, registry, bundle = self.runtime(session_id)
        self.safety_policy.require('agent_execution')
        self.store.charge(session_id, replans=1)
        self.store.update_session(session_id, status='replanning')
        self.running_since[session_id] = time.monotonic()
        completed = [{'step': s.to_dict(), 'state': self.store.step_states(old.plan_id)[s.step_id]} for s in old.steps]
        dispatch = self._planning_dispatch(session_id, bundle, cancelled)
        def context(plan):
            return {'consumed': self.store.budget(session_id), 'step_states': self.store.step_states(old.plan_id)}
        try:
            desktop_provider = getattr(self.services, 'desktop_action_provider', None)
            if desktop_provider is not None:
                desktop_provider.refresh_for_replan(session, old, self.store.step_states(old.plan_id),
                    cancelled=cancelled or (lambda: False))
                registry = self.services.registry(session, bundle)
            with inference_budget(self.services.client, lambda: self._charge_inference(session_id, dispatch)):
                new = AgentPlanner(self.services.client, registry, validation_context=context).create_plan(session, bundle.prompt,
                    cancelled=dispatch, version=old.version + 1, supersedes_plan_id=old.plan_id, completed=completed)
            dispatch()
        except Exception:
            if self.store.get_session(session_id).status != 'cancelled':
                self.store.update_session(session_id, status='blocked')
            raise
        finally:
            started = self.running_since.pop(session_id, None)
            if started is not None:
                self.store.charge_runtime(session_id, time.monotonic() - started)
        self._check_revision(old, new, registry)
        self.store.add_plan(new)
        self.store.carry_completed(old, new)
        emit('agent_replanned', component='agent')
        return new
