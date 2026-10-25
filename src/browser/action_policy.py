"""Closed Gate 3 vocabulary. Page text never grants authority."""
import re
from urllib.parse import urlsplit

from src.agent.models import canonical, snapshot, thaw
from .models import text

SENSITIVE = re.compile(r'password|passcode|one.?time|\botp\b|security.?code|credit.?card|card.?number|\bcvv\b|\bcvc\b|secret|token|session.?id|authentication|social.?security|cc-|iban|routing.?number', re.I)
COMMIT = re.compile(r'submit|send|purchase|buy|pay|checkout|place.?order|delete|remove|destroy|unsubscribe|transfer|publish|confirm|accept|consent|sign|agree', re.I)
SECRET = re.compile(r'sk-[\w-]{12,}|ghp_\w{12,}|Bearer\s+\S+|(?:password|token|secret|otp|cvv|cvc)\s*[:=]', re.I)
ACTIONS = {'set_text':'textbox', 'select_option':'combobox', 'set_checked':'checkbox',
           'scroll':'document', 'controlled_navigation':'link'}


def validate_spec(value, page_origin):
    if type(value) is not dict or set(value) != {'action_type', 'target', 'value'}:
        raise ValueError('Invalid browser action fields.')
    kind, target, proposed = value['action_type'], value['target'], value['value']
    inventory_target = (type(target) is dict and set(target) == {'role', 'name', 'field_ref', 'descriptor_hash'}
                        and kind in ('set_text', 'select_option')
                        and type(target['field_ref']) is str and re.fullmatch('[a-f0-9]{32}', target['field_ref'])
                        and type(target['descriptor_hash']) is str and re.fullmatch('[a-f0-9]{64}', target['descriptor_hash']))
    if (kind not in ACTIONS or type(target) is not dict or (set(target) != {'role', 'name'} and not inventory_target)
            or target['role'] != ACTIONS[kind]):
        raise ValueError('Unsupported browser action or target.')
    text(target['name'], 160)
    if SENSITIVE.search(target['name']) or COMMIT.search(target['name']):
        raise PermissionError('Sensitive or final actions require manual handling.')
    if kind == 'set_checked':
        if type(proposed) is not bool:
            raise ValueError('Expected checkbox state.')
    elif kind == 'scroll':
        if type(proposed) is not int or not -1000 <= proposed <= 1000 or proposed == 0 or target['name'] != 'Page':
            raise ValueError('Expected bounded page scroll.')
    else:
        text(proposed, 1000, empty=kind == 'set_text')
        if SECRET.search(proposed):
            raise PermissionError('Sensitive text must be entered manually.')
        if kind == 'controlled_navigation':
            url = urlsplit(proposed)
            if (url.scheme + '://' + url.netloc != page_origin or url.username or url.password
                    or url.query or url.fragment or '\\' in proposed or any(c.isspace() or ord(c) < 32 for c in proposed)
                    or COMMIT.search(proposed) or SENSITIVE.search(proposed)):
                raise PermissionError('Navigation must use a safe same-origin link without URL parameters.')
    canonical(value, limit=4096)
    return thaw(snapshot(value))


def validate_observation(value):
    if (type(value) is not dict or set(value) != {'signature', 'state'}
            or type(value['signature']) is not str or not re.fullmatch('[a-f0-9]{64}', value['signature'])):
        raise ValueError('Invalid browser observation.')
    state = value['state']
    if type(state) is str:
        text(state, 2048, empty=True)
        if SECRET.search(state):
            raise PermissionError('Sensitive state cannot be previewed.')
    elif type(state) is not bool and not (type(state) is list and len(state) == 2 and all(type(v) in (int, float) and abs(v) <= 10000000 for v in state)):
        raise ValueError('Invalid browser pre-state.')
    return thaw(snapshot(value))
