"""Review grants bind a specific plan, live capability and resolved write inputs."""
from .bindings import references
from .models import digest, thaw
from .validation import AgentPlanValidator
from src.observability.performance import emit


class AgentApprovalService:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def _token(self, plan, session, step, inputs):
        capability = self.registry.get(step.capability_id)
        if capability is None or not capability.availability:
            raise ValueError('Capability changed or became unavailable.')
        return digest({'plan': plan.fingerprint, 'capability': capability.fingerprint,
            'constraints': session.constraints.to_dict(), 'privacy': session.privacy_mode,
            'inputs': inputs if capability.side_effecting or capability.risk_level != 'read_only' else step.to_dict()['input_bindings']})

    def approve_step(self, plan, session, step_id, inputs=None):
        AgentPlanValidator(self.registry).require(plan, session, **self.store.validation_context(plan))
        if self.store.get_session(session.session_id).current_plan_id != plan.plan_id:
            raise ValueError('Only the current plan can be approved.')
        step = next((s for s in plan.steps if s.step_id == step_id), None)
        if step is None:
            raise ValueError('Step does not exist.')
        capability = self.registry.get(step.capability_id)
        resolved = thaw(step.input_bindings) if inputs is None else inputs
        if capability.side_effecting or capability.risk_level != 'read_only':
            if references(resolved):
                raise ValueError('Review resolved write arguments at execution.')
            from .validation import validate_inputs
            validate_inputs(capability.input_schema, resolved)
        self.store.grant(plan.plan_id, step_id, self._token(plan, session, step, resolved))
        emit('agent_plan_approved', component='agent', properties={'step_count': 1})

    def approve_safe(self, plan, session):
        for step in plan.steps:
            capability = self.registry.get(step.capability_id)
            if capability and capability.risk_level == 'read_only' and not capability.side_effecting:
                self.approve_step(plan, session, step.step_id)

    def permits(self, plan, session, step, inputs):
        try:
            return (self.store.get_session(session.session_id).current_plan_id == plan.plan_id and
                    self.store.approval(plan.plan_id, step.step_id) == self._token(plan, session, step, inputs))
        except (ValueError, TypeError):
            return False
