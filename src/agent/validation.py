"""Fail-closed structural, registry, privacy and budget checks."""
from collections.abc import Mapping
from dataclasses import dataclass
from jsonschema import Draft202012Validator
from src.mcp.client.discovery import validate_schema
from .bindings import references
from .models import thaw


@dataclass(frozen=True)
class PlanValidationResult:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    estimated_cost: float | None = None
    ai_calls: int = 0
    external_writes: int = 0


def approval_mode(capability):
    if capability.risk_level in ('unknown', 'destructive'):
        return 'disabled'
    if capability.risk_level == 'read_only' and not capability.side_effecting:
        return 'automatic_safe'
    return 'approve_at_execution'


def validate_inputs(schema, inputs, *, defer_bindings=False):
    raw = thaw(inputs)
    validator = Draft202012Validator(validate_schema(thaw(schema)))
    for error in validator.iter_errors(raw):
        if defer_bindings and isinstance(error.instance, str) and references(error.instance):
            continue
        raise ValueError('Capability input does not match its registered schema.')


class AgentPlanValidator:
    def __init__(self, registry):
        self.registry = registry

    def validate(self, plan, session, *, private=False, consumed=None, step_states=None):
        errors, warnings = [], []
        policy = session.constraints.effective(private=private or session.privacy_mode != 'standard')
        if session.elapsed_runtime_seconds >= policy.max_runtime_minutes * 60:
            errors.append('Mission runtime budget exhausted.')
        if plan.session_id != session.session_id:
            errors.append('Plan belongs to another mission.')
        if len(plan.steps) > policy.max_steps:
            errors.append('Plan exceeds the step budget.')
        ids = [s.step_id for s in plan.steps]
        if len(set(ids)) != len(ids):
            errors.append('Duplicate step IDs.')
        if [s.order for s in plan.steps] != list(range(1, len(plan.steps) + 1)):
            errors.append('Steps must use consecutive dependency-safe order.')
        preceding, ancestry = set(), {}
        writes = ai_calls = ui_actions = ui_commits = 0
        costs = []
        consumed, step_states = consumed or {}, step_states or {}
        for step in plan.steps:
            if len(set(step.dependencies)) != len(step.dependencies) or not set(step.dependencies) <= preceding:
                errors.append('Missing, cyclic or out-of-order dependency.')
            ancestry[step.step_id] = set(step.dependencies)
            for dependency in step.dependencies:
                ancestry[step.step_id].update(ancestry.get(dependency, ()))
            try:
                for ref, path in references(step.input_bindings):
                    if ref not in ancestry[step.step_id]:
                        errors.append('Input binding must refer to a completed dependency.')
            except ValueError:
                errors.append('Unsupported input binding.')
            preceding.add(step.step_id)
            capability = self.registry.get(step.capability_id)
            if step.capability_id in getattr(self.registry, 'historical_capabilities', ()) and not (
                    step_states.get(step.step_id, {}).get('attempted') or step_states.get(step.step_id, {}).get('status') == 'success'):
                errors.append('A completed or attempted desktop capability cannot be scheduled again.')
            if capability is None or not capability.availability:
                errors.append('Capability is unavailable or unregistered.')
                continue
            if capability.capability_type != step.step_type:
                errors.append('Step type does not match its capability.')
            if step.risk_level != capability.risk_level or step.approval_mode != approval_mode(capability):
                errors.append('Risk and approval must match trusted capability metadata.')
            if approval_mode(capability) == 'disabled':
                errors.append('Unclassified or destructive capability is disabled.')
            if capability.side_effecting and step.verification:
                required = 'desktop_postcondition' if step.step_type == 'desktop_action' else 'workflow_status' if step.step_type == 'run_workflow' else 'result_exists'
                if step.verification.get('method', required) != required or step.verification.get('field', 'resource_id') != 'resource_id':
                    errors.append('Write verification cannot be weakened by the plan.')
            if step.capability_id in policy.forbidden_capabilities:
                errors.append('Capability is forbidden by goal constraints.')
            if step.step_type == 'run_workflow' and policy.allowed_workflows is not None and step.capability_id.removeprefix('workflow:') not in policy.allowed_workflows:
                errors.append('Workflow is outside the goal allowlist.')
            if step.step_type == 'retrieve_memory' and session.context_scope == 'authorized_memory_search' and not policy.allow_memory_search:
                errors.append('Memory search is not authorized.')
            if capability.risk_level == 'external_write':
                if step_states.get(step.step_id, {}).get('status') not in ('running', 'success') and not step_states.get(step.step_id, {}).get('attempted'):
                    writes += 1
                if not policy.allow_external_writes:
                    errors.append('External writes are forbidden by current policy.')
            if step.step_type == 'desktop_action':
                ui_actions += 1
                if step.capability_id.startswith('desktop:invoke:') and capability.risk_level == 'external_write':
                    ui_commits += 1
            if capability.risk_level == 'local_write' and not policy.allow_local_writes:
                errors.append('Local writes are forbidden by goal constraints.')
            if getattr(capability, 'remote', False):
                if step.step_type == 'call_mcp_tool' and not policy.allow_remote_mcp:
                    errors.append('Remote MCP is forbidden by current policy.')
                elif step.step_type != 'call_mcp_tool' and not policy.allow_remote_ai:
                    errors.append('Remote processing is forbidden by current policy.')
            if step_states.get(step.step_id, {}).get('status') not in ('running', 'success') and not step_states.get(step.step_id, {}).get('attempted'):
                ai_calls += getattr(capability, 'ai_calls', 0)
            costs.append(step.estimated_cost)
            try:
                validate_inputs(capability.input_schema, step.input_bindings, defer_bindings=True)
            except (ValueError, TypeError, RecursionError):
                errors.append('Invalid capability input schema or bindings.')
        if writes + consumed.get('writes', 0) > policy.max_external_writes:
            errors.append('Plan exceeds the external write budget.')
        if ui_actions > policy.max_ui_actions or ui_commits > policy.max_external_commits:
            errors.append('Plan exceeds the desktop action or commit budget.')
        if ai_calls + consumed.get('ai_calls', 0) > policy.max_ai_calls:
            errors.append('Plan exceeds the AI call budget.')
        estimate = sum(costs) if all(c is not None for c in costs) else None
        if policy.max_estimated_cost is not None and (estimate is None or estimate > policy.max_estimated_cost):
            errors.append('Estimated cost is unavailable or exceeds the goal budget.')
        if estimate is None:
            warnings.append('Estimated AI cost unavailable.')
        return PlanValidationResult(not errors, tuple(dict.fromkeys(errors)), tuple(warnings), estimate, ai_calls, writes)

    def require(self, plan, session, **kwargs):
        result = self.validate(plan, session, **kwargs)
        if not result.valid:
            raise ValueError(' '.join(result.errors))
        return result
