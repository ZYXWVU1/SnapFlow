"""Deterministic observed state → semantic action. UI strings confer no authority."""
from uuid import uuid4
from .models import AutomationSkill, AutomationStepTemplate, TargetTemplate
from src.desktop_actions.models import DesktopActionRequest
from src.desktop_actions.policy import classify


class DemonstrationInterpreter:
    def compile(self, session):
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
