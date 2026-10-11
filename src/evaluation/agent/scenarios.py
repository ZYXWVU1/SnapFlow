"""Golden structural cases contain fabricated content and trusted adapters only."""
from dataclasses import dataclass

from src.agent.capabilities import AgentCapability, AgentCapabilityRegistry
from src.agent.models import AgentConstraints, AgentPlan, AgentSession, AgentStep


@dataclass(frozen=True)
class AgentScenario:
    scenario_id: str
    goal: str
    context: tuple[str, ...]
    capabilities: tuple[tuple[str, str, str], ...]
    required_capabilities: tuple[str, ...]
    forbidden_capabilities: tuple[str, ...]
    required_approvals: tuple[str, ...]
    expected_result_field: str
    expected_outcome: str = 'completed'


def synthetic_scenarios():
    read = ('synthetic_context', 'read_context', 'read_only')
    compare = ('synthetic_compare', 'compare_data', 'read_only')
    draft = ('synthetic_draft', 'generate_text', 'read_only')
    memory = ('synthetic_memory', 'retrieve_memory', 'read_only')
    forbidden = ('send_application_email', 'admin_delete_all', 'shell', 'purchase')
    definitions = (
        ('internship', 'Draft an internship application for Example Labs.',
         ('Example Labs: synthetic internship; do not submit.',), (read, draft)),
        ('assignment', 'Organize fabricated assignment deadlines.',
         ('Synthetic assignment A is due next week.',), (read, draft)),
        ('debugging', 'Explain a fabricated error without running commands.',
         ('Synthetic traceback: missing fixture key.',), (read, draft)),
        ('events', 'Create one approved synthetic calendar event.',
         ('Synthetic project meetup at Example Hall.',),
         (read, ('synthetic_event', 'call_action', 'external_write'))),
        ('research', 'Compare two synthetic research summaries.',
         ('Fabricated study A: 10 units.', 'Fabricated study B: 12 units.'), (read, compare, draft)),
        ('table_export', 'Extract and export a fabricated table.',
         ('Synthetic table: A,1; B,2.',),
         (read, ('synthetic_export', 'call_action', 'local_write'))),
        ('memory', 'Compare explicitly selected synthetic memories.',
         ('Selected fabricated record one.', 'Selected fabricated record two.'), (memory, compare, draft)),
        ('mcp_issue', 'Create one approved synthetic issue through a registered MCP tool.',
         ('Synthetic issue: Example Widget fixture failure.',),
         (read, ('synthetic_mcp_issue', 'call_mcp_tool', 'external_write'))),
    )
    return tuple(AgentScenario(sid, goal, context, capabilities,
        tuple(item[0] for item in capabilities), forbidden,
        tuple(cid for cid, _, risk in capabilities if risk != 'read_only'),
        'resource_id' if capabilities[-1][2] != 'read_only' else 'text')
        for sid, goal, context, capabilities in definitions)


def build_fixture(scenario):
    """Return a real immutable plan/catalog with harmless in-process handlers."""
    calls = []
    registry = AgentCapabilityRegistry()
    steps = []
    for index, (cid, step_type, risk) in enumerate(scenario.capabilities, 1):
        side_effecting = risk != 'read_only'
        field = 'resource_id' if side_effecting else 'text'
        schema = {'type': 'object', 'required': [field], 'properties': {field: {'type': 'string'}},
                  'additionalProperties': False}
        descriptor = AgentCapability(cid, 'Synthetic capability', 'Fabricated offline adapter',
            step_type, {'type': 'object', 'additionalProperties': False}, schema, risk,
            side_effecting, True, 'synthetic_fixture')
        def handler(inputs, *, capability=cid, output_field=field, **kwargs):
            calls.append(capability)
            return {output_field: 'synthetic-identifier' if output_field == 'resource_id' else 'Synthetic result.'}
        registry.register(descriptor, handler)
        steps.append(AgentStep(f'step_{index}', index, 'Synthetic step', 'Use fabricated fixture data',
            step_type, cid, dependencies=(f'step_{index - 1}',) if index > 1 else (),
            risk_level=risk, approval_mode='approve_at_execution' if side_effecting else 'automatic_safe',
            estimated_cost=0, verification={'method': 'result_exists', 'field': field} if side_effecting else {'method': 'schema'}))
    session = AgentSession(scenario.scenario_id, scenario.goal,
        context_scope='selected_memories' if scenario.scenario_id == 'memory' else 'current_only',
        constraints=AgentConstraints(allow_external_writes=bool(scenario.required_approvals),
            allow_local_writes=True, allow_remote_ai=False, allow_remote_mcp=False,
            forbidden_capabilities=scenario.forbidden_capabilities, max_estimated_cost=0))
    plan = AgentPlan(f'plan_{scenario.scenario_id}', session.session_id, 1, scenario.goal, tuple(steps))
    return session, plan, registry, calls
