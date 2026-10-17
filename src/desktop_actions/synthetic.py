"""Deterministic synthetic provider; native Windows validation uses synthetic_app separately."""
from dataclasses import replace
from .models import DesktopActionFence, DesktopElement, DesktopElementCapabilities, DesktopState


class SyntheticDesktopProvider:
    def __init__(self):
        self.window_id, self.serial, self.writes = 42, 1, 0
        self.security_available, self.fail_after_write = True, False
        specs = [('editor', 'Edit', 'Document', ('set_value',), {'value': '', 'read_only': False}),
            ('setting', 'CheckBox', 'Dark Mode', ('toggle',), {'toggle_state': False}),
            ('dark', 'RadioButton', 'Dark', ('select',), {'selected': False, 'selection_container': 'appearance'}),
            ('save', 'Button', 'Save', ('invoke',), {}), ('submit', 'Button', 'Submit', ('invoke',), {}),
            ('delete', 'Button', 'Delete', ('invoke',), {}), ('payment', 'Button', 'Payment', ('invoke',), {}),
            ('password', 'Edit', 'Password', ('set_value',), {}), ('status', 'Text', 'Unsaved', (), {})]
        self.elements = tuple(DesktopElement(key, 100, 42, 'SnapFlowSynthetic.exe', 'ControlType.' + kind,
            name, key, 'Synthetic', ('ControlType.Window|test|Synthetic',), (10, 10, 100, 40), key == 'password',
            {'enabled': True, 'offscreen': False, **state}, DesktopElementCapabilities(actions))
            for key, kind, name, actions, state in specs)

    def fence(self):
        return DesktopActionFence(100, 'SnapFlowSynthetic.exe', 42, 'synthetic_session')

    def target(self, key):
        return next(e for e in self.elements if e.element_id == key).target('synthetic_snapshot')

    def capture(self, fence, *, cancelled=lambda: False):
        return DesktopState(100, 42, 'SnapFlowSynthetic.exe', self.window_id, self.elements, self.security_available)

    def duplicate(self, key):
        element = next(e for e in self.elements if e.element_id == key)
        self.elements += (replace(element, element_id=key + '_duplicate', bounds=(200, 10, 290, 40)),)

    def set_state(self, key, **state):
        self.elements = tuple(replace(e, state={**e.state, **state}) if e.element_id == key else e for e in self.elements)

    def execute(self, request, element, fence, *, cancelled=lambda: False):
        if cancelled():
            raise InterruptedError('Cancelled.')
        self.writes += 1
        if request.action_type == 'set_value':
            self.set_state(element.element_id, value=request.value)
        elif request.action_type == 'toggle':
            self.set_state(element.element_id, toggle_state=request.value)
        elif request.action_type == 'select':
            self.set_state(element.element_id, selected=True)
        elif request.action_type == 'invoke' and element.element_id == 'save':
            self.elements = tuple(replace(e, name='Saved') if e.element_id == 'status' else e for e in self.elements)
        if self.fail_after_write:
            raise TimeoutError('Synthetic lost outcome.')
        return 'uia_pattern'
