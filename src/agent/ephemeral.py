"""Desktop-derived plans/outputs stay in RAM; durable history holds safe receipts."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4
from src.agent.storage import AgentStore


class DesktopAwareAgentStore(AgentStore):
    def __init__(self, path):
        self.temporary_sessions = set()
        self.temporary_plans = {}
        self.temporary_outputs = {}
        self.released_sessions = set()
        self.identifier_salt = uuid4().hex
        super().__init__(path)

    def enable_temporary(self, session_id):
        with self.lock:
            self.temporary_sessions.add(session_id)

    def _opaque_id(self, session_id, step_id):
        return 'temporary_' + sha256((self.identifier_salt + session_id + ':' + step_id).encode()).hexdigest()

    def _durable_id(self, plan_id, step_id):
        plan = super().get_plan(plan_id)
        if plan.session_id not in self.temporary_sessions or any(s.step_id == step_id for s in plan.steps):
            return step_id
        return self._opaque_id(plan.session_id, step_id)

    def _skeleton(self, plan):
        return replace(plan, goal_summary='Temporary desktop plan (contents released on Clear/Stop)',
            assumptions=(), warnings=('Original desktop-derived plan is available only until context is cleared.',),
            steps=tuple(replace(step, step_id=self._opaque_id(plan.session_id, step.step_id),
                dependencies=tuple(self._opaque_id(plan.session_id, sid) for sid in step.dependencies),
                title='Temporary step ' + str(step.order), description='',
                input_bindings={}, expected_output=None, verification=None) for step in plan.steps))

    def add_plan(self, plan):
        with self.lock:
            if plan.session_id in self.released_sessions:
                raise ValueError('Desktop Context was cleared. Create a new mission.')
            temporary = plan.session_id in self.temporary_sessions
            super().add_plan(self._skeleton(plan) if temporary else plan)
            if temporary:
                self.temporary_plans[plan.plan_id] = plan
                if plan.supersedes_plan_id:
                    for (old_id, step_id), values in tuple(self.temporary_outputs.items()):
                        if old_id == plan.supersedes_plan_id:
                            self.temporary_outputs[(plan.plan_id, step_id)] = deepcopy(values)

    def get_plan(self, plan_id):
        with self.lock:
            return self.temporary_plans.get(plan_id) or super().get_plan(plan_id)

    def plans(self, session_id):
        with self.lock:
            return [self.temporary_plans.get(p.plan_id, p) for p in super().plans(session_id)]

    def set_step(self, plan_id, step_id, status, *, output=None, evidence=None, invocation=None):
        with self.lock:
            session_id = super().get_plan(plan_id).session_id
            if session_id not in self.temporary_sessions:
                return super().set_step(plan_id, step_id, status, output=output, evidence=evidence, invocation=invocation)
            if plan_id in self.temporary_plans:
                cached = self.temporary_outputs.setdefault((plan_id, step_id), {})
                if output is not None:
                    cached['output'] = deepcopy(output)
                if evidence is not None:
                    cached['evidence'] = deepcopy(evidence)
            safe_output = {'temporary_output': True} if output is not None else None
            durable_id = self._durable_id(plan_id, step_id)
            state = super().step_states(plan_id)[durable_id]
            if state['attempted'] and isinstance(output, dict):
                if type(output.get('success')) is bool:
                    safe_output['success'] = output['success']
                elif output.get('resource_id') or output.get('status') == 'success':
                    safe_output['success'] = True
            safe_evidence = {'verified': evidence.get('verified') is True,
                'method': 'temporary_desktop_output', 'warnings': ['Desktop-derived details are temporary.']} if evidence else None
            super().set_step(plan_id, durable_id, status, output=safe_output, evidence=safe_evidence, invocation=invocation)

    def step_states(self, plan_id):
        with self.lock:
            states = super().step_states(plan_id)
            if plan_id in self.temporary_plans:
                plan = self.temporary_plans[plan_id]
                states = {step.step_id: dict(states[self._opaque_id(plan.session_id, step.step_id)], id=step.step_id)
                    for step in plan.steps}
            for step_id, state in states.items():
                state.update(deepcopy(self.temporary_outputs.get((plan_id, step_id), {})))
            return states

    def claim_step(self, plan_id, step_id, **kwargs):
        return super().claim_step(plan_id, self._durable_id(plan_id, step_id), **kwargs)

    def grant(self, plan_id, step_id, token):
        return super().grant(plan_id, self._durable_id(plan_id, step_id), token)

    def approval(self, plan_id, step_id):
        return super().approval(plan_id, self._durable_id(plan_id, step_id))

    def carry_completed(self, old_plan, new_plan):
        if old_plan.session_id not in self.temporary_sessions:
            return super().carry_completed(old_plan, new_plan)
        old_steps = {s.step_id: s.to_dict() for s in old_plan.steps}
        new_steps = {s.step_id: s.to_dict() for s in new_plan.steps}
        for sid, state in self.step_states(old_plan.plan_id).items():
            if state['status'] == 'success' or state['attempted']:
                if new_steps.get(sid) != old_steps[sid]:
                    raise ValueError('Completed steps must remain unchanged.')
        # add_plan already atomically copies durable receipts; cache copies are RAM-only.

    def clear_temporary(self, session_id=None):
        with self.lock:
            self.released_sessions.update(self.temporary_sessions if session_id is None else (session_id,))
            identifiers = {pid for pid, plan in self.temporary_plans.items()
                if session_id is None or plan.session_id == session_id}
            for pid in identifiers:
                self.temporary_plans.pop(pid, None)
            for key in tuple(self.temporary_outputs):
                if key[0] in identifiers:
                    self.temporary_outputs.pop(key, None)
            # Keep IDs marked so late operations cannot persist raw values.

    def delete_session(self, session_id):
        super().delete_session(session_id)
        self.clear_temporary(session_id)
