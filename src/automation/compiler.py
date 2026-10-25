"""Deterministic observed state → semantic action. UI strings confer no authority."""
from uuid import uuid4
from .models import AutomationSkill, AutomationStepTemplate, TargetTemplate, BrowserTargetTemplate, BROWSER_ACTIONS
from src.desktop_actions.models import DesktopActionRequest
from src.desktop_actions.policy import classify


class DemonstrationInterpreter:
    def compile(self, session):
        if hasattr(session, 'origin'):
            return self.compile_browser(session)
        if session.status != 'completed' or session.incomplete:
            raise ValueError('Finish teaching before creating a draft.')
        steps = []
        for event in session.events:
            if event.event_type == 'protected_field_interaction_detected':
                raise ValueError('Protected interaction detected. Discard this trace and teach without credentials.')
            before, after = event.before, event.after
            if not after:
                continue
            action, value = None, None
            for prop, candidate in (('toggle_state', 'toggle'), ('value', 'set_value'),
                    ('range_value', 'set_range_value'), ('selected', 'select'), ('expanded', 'expand')):
                if prop in after.state and before.state.get(prop) != after.state[prop]:
                    if prop == 'selected' and after.state[prop] is not True:
                        continue
                    action, value = candidate, after.state[prop]
                    if action == 'select':
                        value = None
                    if prop == 'expanded':
                        action, value = ('expand' if value else 'collapse'), None
                    break
            if event.event_type == 'invoked':
                action, value = 'invoke', None
            if action is None and any(before.state.get('scroll_' + axis) != after.state.get('scroll_' + axis) for axis in ('horizontal', 'vertical')):
                action = 'scroll'
                value = {axis: after.state.get('scroll_' + axis, 0) for axis in ('horizontal', 'vertical')}
            if action is None or action not in after.capabilities.actions:
                continue
            target = TargetTemplate.from_element(after, action)
            request = DesktopActionRequest('inference', target.target(), action, value)
            risk = classify(request, after)
            if risk in ('ui_sensitive', 'ui_destructive'):
                raise ValueError('This demonstration contains unsupported sensitive or destructive behavior.')
            step_id = 'step_' + uuid4().hex
            steps.append(AutomationStepTemplate(step_id, (action.replace('_', ' ').title() + ' ' + after.name)[:200],
                action, target, value, dependencies=(steps[-1].step_id,) if steps else (), risk_level=risk,
                confidence=.55 if event.event_type.endswith('unknown_before') else .95 if action != 'invoke' else .65,
                warnings=('Add a changed outcome condition before testing this invocation.',) if action == 'invoke' else
                ('Previous state unavailable. Confirm this action explicitly.',) if event.event_type.endswith('unknown_before') else ()))
        from src.observability.performance import emit
        emit('demo_steps_inferred', component='skill', properties={'step_count': len(steps)})
        return AutomationSkill(session.goal_description, tuple(steps), supported_apps=session.allowed_apps)

    def compile_browser(self, trace):
        """Compile sanitized semantic evidence; never infer authority from page strings."""
        from src.agent.models import canonical
        from .validation import validate_definition
        if trace.status != 'completed' or trace.incomplete is not False:
            raise ValueError('Finish a complete browser teaching session before creating a draft.')
        if type(trace.events) not in (tuple, list) or not 1 <= len(trace.events) <= 20:
            raise ValueError('A browser draft needs one to twenty bounded semantic steps.')
        steps = []
        for event in trace.events:
            if type(event) is not dict or set(event) != {'action_type', 'target', 'value'}:
                raise ValueError('Only sanitized semantic browser trace events are supported.')
            canonical(event, limit=4096)
            raw = event['target']
            if type(raw) is not dict or set(raw) != {'role', 'name'}:
                raise ValueError('A browser event must identify a semantic role and accessible name.')
            kind = event['action_type']
            if type(kind) is not str:
                raise ValueError('Invalid browser trace action.')
            action = kind if kind == 'manual_checkpoint' or kind.startswith('browser_') else 'browser_' + kind
            if action not in (*BROWSER_ACTIONS, 'manual_checkpoint'):
                raise ValueError('This browser action is unsupported; record a manual checkpoint instead.')
            target = BrowserTargetTemplate(trace.origin, raw['role'], raw['name'])
            manual = action == 'manual_checkpoint'
            if not manual:
                target.spec(action, event['value'])
            step_id = 'step_' + uuid4().hex
            steps.append(AutomationStepTemplate(step_id,
                ('Manual checkpoint: ' if manual else action.removeprefix('browser_').replace('_', ' ').title() + ' ') + target.name_template,
                action, target, event['value'], dependencies=(steps[-1].step_id,) if steps else (),
                risk_level='unknown' if manual else 'ui_navigation' if action in ('browser_scroll', 'browser_controlled_navigation') else 'ui_external_edit',
                confidence=1.0, warnings=('Complete this checkpoint manually. SnapFlow will not execute it.',) if manual else ()))
        return validate_definition(AutomationSkill(trace.goal_description, tuple(steps), supported_origins=(trace.origin,)))
