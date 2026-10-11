"""Exercise the real validator, approval, executor and store with fake services."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile

from src.agent.approvals import AgentApprovalService
from src.agent.executor import AgentExecutor
from src.agent.storage import AgentStore
from src.agent.verification import AgentVerifier
from src.reliability.journal import ExecutionJournal
from src.reliability.policy import SafetyPolicy
from .metrics import aggregate_metrics, execution_metrics, planning_metrics
from .scenarios import build_fixture, synthetic_scenarios


def _evaluate(scenario, directory):
    session, plan, registry, calls = build_fixture(scenario)
    store = AgentStore(directory / 'missions.sqlite3')
    journal = ExecutionJournal(directory / 'journal.sqlite3')
    store.create_session(session)
    store.add_plan(plan)
    executor = AgentExecutor(store, registry, journal, safety_policy=SafetyPolicy())
    planned = planning_metrics(plan, session, registry, scenario)
    approvals = AgentApprovalService(store, registry)
    executor.execute(session.session_id)
    approval_correct = not calls
    approvals.approve_safe(plan, session)
    executor.execute(session.session_id)
    approval_correct = approval_correct and not any(cid in calls for cid in scenario.required_approvals)
    unauthorized = sum(cid in scenario.required_approvals for cid in calls)
    for step in plan.steps:
        if step.capability_id in scenario.required_approvals:
            approvals.approve_step(plan, session, step.step_id, {})
    report = executor.execute(session.session_id)
    executor.execute(session.session_id)
    verifier = AgentVerifier()
    accurate = all(verifier.verify(step, registry.get(step.capability_id),
        store.step_states(plan.plan_id)[step.step_id]['output']).verified and
        not verifier.verify(step, registry.get(step.capability_id), {}).verified for step in plan.steps)
    before_replan = list(calls)
    revised = replace(plan, plan_id=plan.plan_id + '_v2', version=2, supersedes_plan_id=plan.plan_id)
    store.add_plan(revised)
    approvals.approve_safe(revised, store.get_session(session.session_id))
    executor.execute(session.session_id)
    safe_replan = calls == before_replan and all(state['status'] == 'success'
        for state in store.step_states(revised.plan_id).values())
    with store.connection() as db:
        budget = dict(db.execute('SELECT * FROM budgets WHERE session=?', (session.session_id,)).fetchone())
    budget_violations = sum(budget[key] > maximum for key, maximum in
        (('ai_calls', session.constraints.max_ai_calls), ('writes', session.constraints.max_external_writes),
         ('replans', session.constraints.max_replans)))
    measured = execution_metrics(report, scenario, calls, registry,
        unauthorized_action_count=unauthorized, approval_correctness=approval_correct,
        verification_accuracy=accurate, replanning_safety=safe_replan)
    return {'scenario_id': scenario.scenario_id, **planned, **measured,
            'budget_violation_count': budget_violations, 'replan_rate': 1.0}


def run_synthetic_evaluation(*, output=None, work_dir=None):
    """No network, user data, live model, shell tool or third-party API is used.

    This measures deterministic fixtures, not model planning quality or live
    semantic correctness. The real JSON planner's adversarial tests are a
    separate release gate. Optional reports contain counts and case IDs only.
    """
    with tempfile.TemporaryDirectory(prefix='snapflow-agent-evaluation-', dir=work_dir) as temporary:
        directory = Path(temporary)
        results = [_evaluate(case, directory / case.scenario_id) for case in synthetic_scenarios()]
    metrics = aggregate_metrics(results)
    rate_keys = ('plan_validity_rate', 'capability_compliance_rate', 'approval_correctness_rate',
        'constraint_compliance_rate', 'execution_success_rate', 'verification_accuracy_rate',
        'replanning_safety_rate', 'risk_classification_correctness_rate', 'verification_success_rate')
    passed = all(metrics[key] == 1 for key in rate_keys) and all(metrics[key] == 0 for key in
        ('unauthorized_action_count', 'external_write_duplication_count', 'budget_violation_count'))
    report = {'schema_version': 1, 'qualification_scope': 'synthetic_offline',
              'status': 'PASS' if passed else 'FAIL', 'metrics': metrics, 'scenarios': results,
              'live_model_inference': 'NOT VERIFIED', 'live_provider_effects': 'NOT VERIFIED'}
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report
