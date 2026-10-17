"""Controlled procedure orchestration. All effects pass through Phase 16, one approval at a time."""
from dataclasses import dataclass, field, replace
from threading import Event
from uuid import uuid4

from src.desktop_actions.models import DesktopActionRequest, DesktopCondition
from src.agent.models import snapshot, digest
from .parameters import bind, validate_inputs, references


@dataclass
class AutomationRun:
    skill: object
    inputs: dict
    run_id: str = field(default_factory=lambda: 'run_' + uuid4().hex)
    index: int = 0
    status: str = 'ready'
    results: list = field(default_factory=list)
    stopped: Event = field(default_factory=Event)
    requests: dict = field(default_factory=dict)
    identity: str = ''
    max_actions: int = 20
    max_commits: int = 0
    commits: int = 0
    validated_previews: set = field(default_factory=set)


class AutomationRuntime:
    def __init__(self, service, *, enabled=lambda: False):
        self.service, self.enabled = service, enabled

    def _guard(self, run=None):
        if not self.enabled() or run and (run.stopped.is_set() or run.status not in ('ready', 'running')):
            raise PermissionError('Automation stopped. Review before starting a new run.')
        if run and run.identity != digest([run.skill.to_dict(), run.inputs]):
            raise PermissionError('Skill or inputs changed. Review a new run.')
        return self.service._boundary()

    def prepare(self, skill, inputs):
        fence = self._guard()
        if self.service.journal.pending_review():
            raise PermissionError('Review interrupted or uncertain writes in Recovery before starting another procedure.')
        if skill.status in ('disabled', 'archived') or skill.trust == 'imported_unreviewed':
            raise PermissionError('Review and test this disabled/imported skill first.')
        values = validate_inputs(skill.inputs, inputs)
        if not references(skill.to_dict()) <= set(values):
            raise ValueError('Skill references undeclared or unavailable inputs.')
        run = AutomationRun(skill, snapshot(values))
        run.max_actions, run.max_commits = fence.max_ui_actions, fence.max_external_commits
        run.identity = digest([skill.to_dict(), run.inputs])
        return run

    def _request(self, run, index):
        fence = self._guard(run)
        step = run.skill.steps[index]
        target = step.target_template.target(lambda value: bind(value, run.inputs))
        # Another application remains unresolved until its window fence is reviewed.
        key = (index, fence.fence_id)
        if key in run.requests:
            return run.requests[key]
        condition = lambda c: DesktopCondition(c.target.target(lambda value: bind(value, run.inputs)), c.property, bind(c.equals, run.inputs))
        request = DesktopActionRequest(run.run_id + '_' + str(index), target, step.action_type, bind(step.value_template, run.inputs),
            agent_session_id=fence.session_id, agent_step_id=step.step_id,
            expected_preconditions=tuple(condition(c) for c in (*run.skill.preconditions, *step.preconditions)),
            expected_postconditions=tuple(condition(c) for c in (*step.postconditions,
                *(run.skill.postconditions if index == len(run.skill.steps) - 1 else ()))))
        run.requests[key] = request
        return request

    def dry_run(self, run):
        return tuple(self.preview(run, i) for i in range(run.index, len(run.skill.steps)))

    def preview(self, run, index=None):
        index = run.index if index is None else index
        fence = self._guard(run)
        # A fresh human review renews the unattempted request, revoking older
        # previews/tokens while retaining the journal identity of this step.
        previous = run.requests.pop((index, fence.fence_id), None)
        if previous:
            for mapping in (self.service.previews, self.service.approvals):
                for key, old in tuple(mapping.items()):
                    if old.request.action_id == previous.action_id:
                        mapping.pop(key, None)
                        run.validated_previews.discard(old.preview_id)
        request = self._request(run, index)
        already_satisfied = False
        missing_witness = False
        if request.action_type == 'invoke' and request.expected_postconditions:
            fence = self._guard(run)
            state = self.service._state(fence)
            already_satisfied = self.service.verifier.matches(request.expected_postconditions, state)
            witnesses = []
            for condition in request.expected_postconditions:
                resolution = self.service.resolver.resolve(condition.target, state)
                if resolution.status == 'resolved':
                    element = resolution.resolved_element
                    actual = element.name if condition.property == 'name' else element.state.get(condition.property)
                    witnesses.append(DesktopCondition(condition.target, condition.property, actual))
                else:
                    missing_witness = True
            step = run.skill.steps[index]
            original = tuple(DesktopCondition(c.target.target(lambda value: bind(value, run.inputs)), c.property, bind(c.equals, run.inputs))
                for c in (*run.skill.preconditions, *step.preconditions))
            request = replace(request, expected_preconditions=(*original, *witnesses))
            run.requests[(index, fence.fence_id)] = request
        preview = self.service.preview(request)
        if preview.allowed and missing_witness:
            self.service.previews.pop(preview.preview_id, None)
            return replace(preview, allowed=False, safe_error='A required invocation outcome control is missing or ambiguous. Repair verification before executing.')
        if preview.allowed and already_satisfied:
            self.service.previews.pop(preview.preview_id, None)
            return replace(preview, allowed=False, safe_error='The invocation outcome is already present. Review it instead of repeating a possible commit.')
        target = run.skill.steps[index].target_template
        element = preview.resolution.resolved_element
        if preview.allowed and (preview.resolution.confidence < target.confidence_threshold or
                not set(target.expected_actions) <= set(element.capabilities.actions) or
                not set(target.expected_patterns) <= set(element.capabilities.patterns)):
            self.service.previews.pop(preview.preview_id, None)
            return replace(preview, allowed=False, safe_error='Target capabilities changed. Repair this skill in a new draft.')
        if preview.allowed and target.window_match != 'optional':
            from fnmatch import fnmatchcase
            actual = element.risk_context[0] if element.risk_context else ''
            expected = bind(target.window_name, run.inputs)
            matches = actual == expected if target.window_match == 'exact' else expected in actual if target.window_match == 'contains' else fnmatchcase(actual, expected)
            if not matches:
                self.service.previews.pop(preview.preview_id, None)
                return replace(preview, allowed=False, safe_error='The selected window does not match this skill. Review a matching fence or repair a new draft.')
        if preview.allowed:
            run.validated_previews.add(preview.preview_id)
        return preview

    def next_request(self, run):
        return self._request(run, run.index)

    def execute_next(self, run, approval_token, *, cancelled=lambda: False, guard=None):
        self._guard(run)
        if run.index >= run.max_actions:
            raise PermissionError('The procedure action budget is exhausted.')
        request = self.next_request(run)
        approval = self.service.approvals.get(approval_token)
        if request.action_type == 'invoke' and (approval is None or approval.preview_id not in run.validated_previews):
            raise PermissionError('Review the invocation outcome through the Automation preview first.')
        if approval and approval.risk_level == 'ui_external_commit':
            if run.commits >= run.max_commits:
                raise PermissionError('The procedure external commit budget is exhausted.')
            run.commits += 1
        def boundary():
            self._guard(run)
            if guard:
                guard()
        result = self.service.execute(request, approval_token,
            cancelled=lambda: run.stopped.is_set() or cancelled(), guard=boundary)
        run.results.append(result.receipt())
        if result.status == 'success':
            run.index += 1
            run.status = 'completed' if run.index == len(run.skill.steps) else 'ready'
        else:
            run.status = 'paused'
        return result

    def stop(self, run):
        run.stopped.set()
        run.inputs = {}
        run.requests.clear()
        run.status = 'stopped'
        self.service.stop()
