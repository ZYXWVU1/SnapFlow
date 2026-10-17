"""Outcome verification from fresh properties, never from successful dispatch."""
from .models import DesktopVerification


class DesktopActionVerifier:
    def __init__(self, resolver):
        self.resolver = resolver

    def matches(self, conditions, state):
        for condition in conditions:
            resolution = self.resolver.resolve(condition.target, state)
            if resolution.status != 'resolved':
                return False
            element = resolution.resolved_element
            actual = element.name if condition.property == 'name' else element.state.get(condition.property)
            if actual != condition.equals:
                return False
        return True

    def verify(self, request, before, after):
        if not after.security_available or after.foreground_window != after.window_id:
            return DesktopVerification(False, 'fresh_state', 'The target window or security boundary changed.')
        resolution = self.resolver.resolve(request.target, after)
        element = resolution.resolved_element if resolution.status == 'resolved' else None
        automatic = {'set_value': ('value', request.value), 'toggle': ('toggle_state', request.value),
            'select': ('selected', True), 'expand': ('expanded', True), 'collapse': ('expanded', False),
            'focus': ('focused', True), 'set_range_value': ('range_value', request.value),
            'scroll_into_view': ('offscreen', False)}
        verified = False
        if request.action_type in automatic and element:
            key, expected = automatic[request.action_type]
            verified = element.state.get(key) == expected
        elif request.action_type == 'scroll' and element:
            verified = all(abs(element.state.get('scroll_' + k, -1000) - v) < .1 for k, v in request.value.items())
        elif request.action_type == 'invoke' and request.expected_postconditions:
            # An already true label is not evidence that this Invoke produced an outcome.
            verified = not self.matches(request.expected_postconditions, before) and self.matches(request.expected_postconditions, after)
        if request.expected_postconditions:
            verified = verified and self.matches(request.expected_postconditions, after)
        return DesktopVerification(bool(verified), 'uia_postcondition',
            'Fresh UI state satisfies the reviewed outcome.' if verified else
            'Execution may have occurred; the intended outcome is unverified. Review before replanning.')
