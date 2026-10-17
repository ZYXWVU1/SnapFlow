"""Trusted risk floors; accessible text can increase risk but cannot authorize actions."""
import re
from src.desktop_context.filters import _CREDENTIAL

_DESTRUCTIVE = re.compile(r'\b(delete|destroy|erase|remove|uninstall|reset|format|wipe)\b', re.I)
_PROHIBITED = re.compile(r'\b(pay|payments?|purchase|buy|checkout|place order|charge|donate|transfer|trade|trading|invest|bank|brokerage|login|log in|sign in|authenticate|captcha|security|execute|run|command|terminal|shell|administrator|elevat)\b', re.I)
_COMMIT = re.compile(r'\b(send|submit|post|publish|upload|confirm|create|share|sync)\b', re.I)
_LOCAL = frozenset(('notepad.exe', 'calculatorapp.exe', 'calc.exe', 'snapflow.exe', 'snapflowsynthetic.exe'))


def classify(request, element, *, local_processes=()):
    label = ' '.join((element.name, element.automation_id, *element.ancestor_path, *element.risk_context))
    denied_processes = ('cmd.exe', 'powershell.exe', 'pwsh.exe', 'windowsterminal.exe', 'wsl.exe', 'putty.exe',
        'mstsc.exe', 'consent.exe', 'credentialuibroker.exe', 'logonui.exe', 'keepass.exe', '1password.exe', 'bitwarden.exe')
    if element.process_name.casefold() in denied_processes or element.protected or _CREDENTIAL.search(label) or _PROHIBITED.search(label):
        return 'ui_sensitive'
    if _DESTRUCTIVE.search(label):
        return 'ui_destructive'
    if request.action_type in ('focus', 'scroll', 'scroll_into_view', 'expand', 'collapse'):
        return 'ui_navigation'
    if request.action_type == 'invoke' and _COMMIT.search(label):
        return 'ui_external_commit'
    local = element.process_name.casefold() in _LOCAL | frozenset(p.casefold() for p in local_processes)
    if request.action_type in ('set_value', 'set_range_value', 'toggle', 'select'):
        return 'ui_local_edit' if local else 'ui_external_edit'
    if request.action_type == 'invoke':
        return 'ui_local_edit' if local else 'unknown'
    return 'unknown'


def review_state(request, element):
    from src.agent.models import digest
    state = dict(element.state)
    if request.action_type != 'focus':
        state.pop('focused', None)
    return digest([element.fingerprint, state])


def validate_action(request, element):
    state = element.state
    if request.action_type not in element.capabilities.actions:
        return 'The control does not expose this structured UI Automation action.'
    if state.get('enabled') is not True:
        return 'The target control is disabled or unavailable.'
    if state.get('offscreen') is not False and request.action_type != 'scroll_into_view':
        return 'The target is offscreen. Review a separate Scroll Into View action.'
    if request.action_type == 'set_value' and state.get('read_only') is not False:
        return 'The target is not safely editable.'
    if request.action_type == 'set_value' and state.get('value_truncated'):
        return 'The current document exceeds the safe review limit. Whole-control replacement is unavailable.'
    if request.action_type == 'set_range_value' and (state.get('read_only') is not False or
            not state.get('minimum', float('inf')) <= request.value <= state.get('maximum', float('-inf'))):
        return 'The requested value is outside the editable range.'
    if request.action_type == 'select' and not state.get('selection_container'):
        return 'The selection container is unavailable.'
    return None
