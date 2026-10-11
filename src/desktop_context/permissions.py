"""Preferences persist; grants never do. Consent is checked before each read."""
from pathlib import PureWindowsPath
from threading import RLock

SOURCES = ('ui', 'selection', 'clipboard')
POLICIES = ('disabled', 'ask', 'session')
BUILTIN_BLOCKED = frozenset(('1password.exe', 'bitwarden.exe', 'keepass.exe', 'keepassxc.exe',
    'lastpass.exe', 'credentialuibroker.exe', 'logonui.exe', 'mstsc.exe'))


def app_key(name):
    return PureWindowsPath(name or '').name.casefold()


def validate_preferences(metadata, ui, selection, clipboard, blocked, apps):
    if type(metadata) is not bool or any(p not in POLICIES for p in (ui, selection, clipboard)):
        raise ValueError('Invalid Desktop Context policy.')
    if not isinstance(blocked, (tuple, list)) or len(blocked) > 100 or any(
            not isinstance(x, str) or not x or len(x) > 128 or app_key(x) != x.casefold() for x in blocked):
        raise ValueError('Use up to 100 process names in the blocked application list.')
    if not isinstance(apps, dict) or len(apps) > 100:
        raise ValueError('Use up to 100 per-application policies.')
    for name, policies in apps.items():
        if not isinstance(name, str) or not name or len(name) > 128 or app_key(name) != name.casefold():
            raise ValueError('Per-app policies require process names.')
        if not isinstance(policies, dict) or set(policies) - {'metadata', *SOURCES}:
            raise ValueError('Invalid per-app policy fields.')
        for source, policy in policies.items():
            if (source == 'metadata' and type(policy) is not bool) or (source != 'metadata' and policy not in POLICIES):
                raise ValueError('Invalid per-app source policy.')


class DesktopContextPermissions:
    def __init__(self):
        self.lock = RLock()
        self.version = 0
        self.metadata = True
        self.policies = dict.fromkeys(SOURCES, 'disabled')
        self.blocked = BUILTIN_BLOCKED
        self.apps = {}
        self.grants = frozenset()

    def configure(self, *, metadata=True, ui='disabled', selection='disabled', clipboard='disabled', blocked=(), apps=None):
        apps = apps or {}
        validate_preferences(metadata, ui, selection, clipboard, blocked, apps)
        with self.lock:
            self.metadata = metadata
            self.policies = dict(zip(SOURCES, (ui, selection, clipboard)))
            self.blocked = BUILTIN_BLOCKED | {app_key(x) for x in blocked}
            self.apps = {app_key(k): dict(v) for k, v in apps.items()}
            self.grants = frozenset()
            self.version += 1

    def grant_session(self, sources):
        sources = tuple(sources)
        if set(sources) - set(SOURCES):
            raise ValueError('Unknown desktop source grant.')
        with self.lock:
            self.grants = frozenset(sources)
            self.version += 1

    def revoke(self):
        with self.lock:
            self.grants = frozenset()
            self.version += 1

    def is_blocked(self, app):
        return not app_key(app) or app_key(app) in self.blocked

    def policy(self, source, app):
        return self.apps.get(app_key(app), {}).get(source,
            self.metadata if source == 'metadata' else self.policies[source])

    def allows(self, source, app, once=()):
        with self.lock:
            if self.is_blocked(app):
                return False
            policy = self.policy(source, app)
            if source == 'metadata':
                return policy is True
            return policy == 'ask' and source in once or policy == 'session' and source in self.grants
