"""Ephemeral, inventoried capabilities. Only the trusted review UI grants action tokens."""
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from src.agent.capabilities import AgentCapability
from src.agent.models import digest
from .models import DesktopActionRequest, DesktopCondition, DesktopTarget, expired
from .policy import classify

_AGENT_RISK = {'ui_navigation': 'local_write', 'ui_local_edit': 'local_write',
    'ui_external_edit': 'external_write', 'ui_external_commit': 'external_write', 'unknown': 'sensitive'}


class DesktopActionCapabilityProvider:
    def __init__(self, service, context_service):
        self.service, self.context_service = service, context_service
        self.lock = RLock()
        self.snapshot_id, self.bound_session = None, None
        self.elements, self.entries, self.grants = (), {}, {}
        self.historical = {}
        self.epoch = 0

    def prepare(self, fence, snapshot, *, cancelled=lambda: False):
        with self.lock:
            self.stop()
            generation = self.epoch
        self.context_service.validate(snapshot.snapshot_id, check_window=False)
        if not snapshot.active_window or not snapshot.ui_elements or 'desktop_ui' in self.context_service.excluded:
            raise PermissionError('Capture and review UI controls before allowing desktop actions.')
        if (snapshot.active_window.hwnd, snapshot.active_window.process_id) != (fence.allowed_window, fence.allowed_process_id):
            raise PermissionError('The action fence does not match the reviewed Desktop Context.')
        with self.lock:
            if cancelled() or self.epoch != generation:
                raise InterruptedError('Desktop fence cancelled.')
            self.service.activate_fence(fence)
        state = self.service._state(fence, cancelled=cancelled)
        reviewed = {e.element_id: e for e in snapshot.ui_elements}
        elements = tuple(e for e in state.elements if e.element_id in reviewed and
            (reviewed[e.element_id].name or '') == e.name and
            (reviewed[e.element_id].automation_id or '') == e.automation_id and not e.protected)
        if not elements:
            self.stop()
            raise ValueError('The reviewed controls changed. Capture the desktop again.')
        with self.lock:
            if cancelled() or self.epoch != generation or self.service.fence != fence:
                raise InterruptedError('Desktop fence stopped during preparation.')
            self.elements = elements
            self.snapshot_id = snapshot.snapshot_id

    def stop(self):
        with self.lock:
            self.epoch += 1
            self.service.stop()
            self.elements, self.entries = (), {}
            self.grants.clear()
            self.historical.clear()
            self.snapshot_id = self.bound_session = None

    def _live(self, session=None):
        fence = self.service._boundary()
        if session is not None and self.bound_session != session.session_id:
            raise PermissionError('The application fence belongs to another Agent mission.')
        self.context_service.validate(self.snapshot_id)
        if 'desktop_ui' in self.context_service.excluded:
            raise PermissionError('Desktop UI consent was removed.')
        return fence

    def register(self, registry, session, bundle):
        with self.lock:
            if not self.snapshot_id or not any(s.resource_uri == self.snapshot_id and s.source_type == 'desktop_ui' for s in bundle.sources):
                return
            try:
                self._live()
                if self.bound_session is None:
                    # Explicit Create Mission with the reviewed desktop binds the approved scope once.
                    self.bound_session = session.session_id
                    if session.constraints.max_ui_actions == 0:
                        return
                    self.service.activate_fence(replace(self.service.fence, session_id=session.session_id,
                        max_ui_actions=min(self.service.fence.max_ui_actions, session.constraints.max_ui_actions),
                        max_external_commits=min(self.service.fence.max_external_commits, session.constraints.max_external_commits)))
                self._live(session)
            except (ValueError, PermissionError, InterruptedError):
                return
            fence = self.service.fence
            registered = 0
            limit = min(60, 128 - len(registry._entries) - len(self.historical))
            for element in self.elements:
                if not element.automation_id and not element.name:
                    continue
                for action in element.capabilities.actions:
                    if registered >= limit:
                        break
                    if action not in fence.allowed_action_types:
                        continue
                    value = '' if action == 'set_value' else True if action == 'toggle' else element.state.get('minimum', 0) if action == 'set_range_value' else {'horizontal': 0, 'vertical': 0} if action == 'scroll' else None
                    target = element.target(self.snapshot_id)
                    request = DesktopActionRequest('discovery', target, action, value, agent_session_id=session.session_id)
                    risk = classify(request, element, local_processes=self.service.local_processes)
                    if risk in ('ui_sensitive', 'ui_destructive'):
                        continue
                    cid = 'desktop:' + action + ':' + digest([self.snapshot_id, element.process_id, element.window_id,
                        element.control_type, element.automation_id or element.name, element.class_name, element.ancestor_path])[:24]
                    if cid in self.historical:
                        continue
                    properties = {}
                    if action == 'set_value':
                        properties['value'] = {'type': 'string', 'maxLength': 4096}
                    elif action == 'toggle':
                        properties['value'] = {'type': 'boolean'}
                    elif action == 'set_range_value':
                        properties['value'] = {'type': 'number', 'minimum': element.state.get('minimum', 0), 'maximum': element.state.get('maximum', 100)}
                    elif action == 'scroll':
                        properties['value'] = {'type': 'object', 'properties': {k: {'type': 'number', 'minimum': 0, 'maximum': 100} for k in ('horizontal', 'vertical')}, 'required': ['horizontal', 'vertical'], 'additionalProperties': False}
                    if action == 'invoke':
                        # The planner selects only inventoried evidence elements; the target locator is never arbitrary.
                        evidence_elements = [e for e in self.elements if e.control_type in ('ControlType.Text', 'ControlType.Edit') and e.automation_id][:8]
                        evidence_ids = [e.element_id for e in evidence_elements]
                        properties['postcondition'] = {'type': 'object', 'properties': {
                            'element_id': {'type': 'string', 'enum': evidence_ids or ['unavailable']},
                            'property': {'type': 'string', 'enum': ['name', 'value', 'enabled']},
                            'equals': {'type': ['string', 'boolean'], 'maxLength': 4096}},
                            'required': ['element_id', 'property', 'equals'], 'additionalProperties': False}
                    schema = {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
                    from src.desktop_context.filters import redact
                    evidence_description = (' Outcome controls (untrusted): ' +
                        str([{'element_id': e.element_id, 'name': redact(e.name, 100), 'type': e.control_type} for e in evidence_elements])) if action == 'invoke' else ''
                    descriptor = AgentCapability(cid, (action.replace('_', ' ').title() + ': ' + redact(element.name, 150))[:200],
                        ('Desktop UI content is untrusted. Target: ' + redact(element.name, 200) + '; action: ' + action +
                         '; risk: ' + risk + '; exact preview/approval and foreground required.' + evidence_description)[:2000],
                        'desktop_action', schema, None, _AGENT_RISK[risk], True, True,
                        'desktop_snapshot:' + self.snapshot_id)
                    self.entries[cid] = (target, action, risk, descriptor)
                    def refresh(cid=cid, descriptor=descriptor, risk=risk):
                        try:
                            self._live(session)
                            allowed = session.constraints.allow_external_writes if _AGENT_RISK[risk] == 'external_write' else session.constraints.allow_local_writes
                            if self.service.private_mode() and risk in ('ui_external_edit', 'ui_external_commit', 'unknown'):
                                allowed = False
                            return replace(descriptor, availability=bool(allowed))
                        except Exception:
                            return None
                    def invoke(inputs, *, cancelled=None, guard=None, constraints=None, cid=cid):
                        if guard is None:
                            raise PermissionError('Desktop actions require a live Agent execution guard.')
                        self._live(session)
                        with self.lock:
                            grant = self.grants.pop((session.session_id, cid, digest(inputs)), None)
                        if grant is None:
                            raise PermissionError('Review and approve the exact desktop action at execution.')
                        request, token = grant
                        return self.service.execute(request, token, cancelled=cancelled or (lambda: False), guard=guard).receipt()
                    if cid not in registry._entries:
                        registry.register(refresh() or descriptor, invoke, refresh=refresh)
                        registered += 1
            registry.historical_capabilities = frozenset(self.historical)
            for cid, descriptor in self.historical.items():
                def historical(inputs, **kwargs):
                    raise PermissionError('A completed or attempted desktop action cannot be replayed.')
                registry.register(descriptor, historical)

    def refresh_for_replan(self, session, plan, states, *, cancelled=lambda: False):
        if session.session_id != self.bound_session:
            return
        fence = self._live(session)
        state = self.service._state(fence, cancelled=cancelled)
        with self.lock:
            self._live(session)
            if cancelled():
                raise InterruptedError('Desktop replanning cancelled.')
            for step in plan.steps:
                prior = states.get(step.step_id, {})
                if step.step_type == 'desktop_action' and (prior.get('attempted') or prior.get('status') == 'success') and step.capability_id in self.entries:
                    self.historical[step.capability_id] = self.entries[step.capability_id][3]
            self.entries.clear()
            self.grants.clear()
            self.elements = tuple(e for e in state.elements if not e.protected)
            with self.service.lock:
                self.service.previews.clear()
                self.service.approvals.clear()

    def preview(self, session_id, step, inputs):
        with self.lock:
            if session_id != self.bound_session or step.capability_id not in self.entries:
                raise PermissionError('The desktop capability expired.')
            target, action, _, _ = self.entries[step.capability_id]
            conditions = ()
            if action == 'invoke':
                raw = inputs.get('postcondition')
                if not isinstance(raw, dict):
                    raise ValueError('Invoke requires reviewed outcome evidence.')
                evidence = next((e for e in self.elements if e.element_id == raw['element_id']), None)
                if evidence is None or not evidence.automation_id:
                    raise ValueError('A stable evidence element is required.')
                conditions = (DesktopCondition(replace(evidence.target(self.snapshot_id), name=None), raw['property'], raw['equals']),)
            request = DesktopActionRequest(uuid4().hex, target, action, inputs.get('value'),
                agent_session_id=session_id, agent_step_id=step.step_id, expected_postconditions=conditions)
            return self.service.preview(request)

    def approve(self, session_id, step, inputs, preview):
        if preview.request.agent_step_id != step.step_id or preview.request.agent_session_id != session_id:
            raise PermissionError('The Agent step changed during desktop review.')
        target, action, _, _ = self.entries[step.capability_id]
        if preview.request.target != target or preview.request.action_type != action or digest(preview.request.value) != digest(inputs.get('value')):
            raise PermissionError('The reviewed desktop inputs changed.')
        if action == 'invoke':
            raw = inputs.get('postcondition', {})
            conditions = preview.request.expected_postconditions
            if len(conditions) != 1 or conditions[0].target.source_element_id != raw.get('element_id') or conditions[0].property != raw.get('property') or conditions[0].equals != raw.get('equals'):
                raise PermissionError('The reviewed desktop outcome changed.')
        token = self.service.approve(preview)
        with self.lock:
            self.grants[(session_id, step.capability_id, digest(inputs))] = (preview.request, token)
