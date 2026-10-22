"""Explicit native-host manifests and per-user, ownership-aware registration."""
import json
import os
from pathlib import Path
import re

from .protocol import HOST_NAME, extension_origin

REGISTRY_PATHS = {
    'chrome': r'Software\Google\Chrome\NativeMessagingHosts' + '\\' + HOST_NAME,
    'edge': r'Software\Microsoft\Edge\NativeMessagingHosts' + '\\' + HOST_NAME,
}


def build_host_manifest(executable, *, chrome_ids=(), edge_ids=()):
    host = Path(executable)
    if not host.is_absolute() or not host.is_file() or host.name.casefold() != 'snapflowbrowserhost.exe':
        raise ValueError('Select the installed absolute SnapFlowBrowserHost.exe path.')
    ids = tuple(dict.fromkeys((*chrome_ids, *edge_ids)))
    if not ids or len(ids) > 16 or any(type(value) is not str or not re.fullmatch('[a-p]{32}', value) for value in ids):
        raise ValueError('Supply actual Chrome/Edge extension IDs (32 letters a–p); no wildcards or placeholders.')
    return {'name': HOST_NAME, 'description': 'SnapFlow Browser Companion Native Host',
            'path': str(host.resolve()), 'type': 'stdio',
            'allowed_origins': ['chrome-extension://' + value + '/' for value in ids]}


def read_host_manifest(path, executable):
    from src.native_host.protocol import _pairs
    target = Path(path)
    if target.stat().st_size > 16384:
        raise ValueError('Native host manifest exceeds limit.')
    value = json.loads(target.read_text(encoding='utf-8'), object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != {'name', 'description', 'path', 'type', 'allowed_origins'}
            or value['name'] != HOST_NAME or value['type'] != 'stdio'
            or type(value['description']) is not str or not 0 < len(value['description']) <= 200
            or type(value['path']) is not str or not Path(value['path']).is_absolute()
            or Path(value['path']).resolve() != Path(executable).resolve()
            or not Path(executable).is_file() or type(value['allowed_origins']) is not list
            or not 0 < len(value['allowed_origins']) <= 16):
        raise ValueError('Invalid or mismatched native host manifest.')
    for caller in value['allowed_origins']:
        extension_origin(caller)
    if len(set(value['allowed_origins'])) != len(value['allowed_origins']):
        raise ValueError('Duplicate approved extension origin.')
    return value


def registration_plan(manifest_path, *, chrome=False, edge=False):
    target = Path(manifest_path).resolve()
    if not target.is_file() or not chrome and not edge:
        raise ValueError('Select an existing host manifest and at least one browser.')
    return tuple({'key': REGISTRY_PATHS[browser], 'manifest': str(target)}
                 for browser, enabled in (('chrome', chrome), ('edge', edge)) if enabled)


def apply_registration(plan, registry):
    # Preflight every selected browser before changing any registration.
    previous = {item['key']: registry.get(item['key']) for item in plan}
    if any(previous[item['key']] not in (None, item['manifest']) for item in plan):
        raise PermissionError('An existing native host registration points elsewhere; it was preserved.')
    written = []
    try:
        for item in plan:
            registry.set(item['key'], item['manifest'])
            written.append(item)
    except OSError:
        for item in reversed(written):
            if previous[item['key']] is None:
                registry.remove_if_owned(item['key'], item['manifest'])
        raise


def remove_registration(plan, registry):
    return tuple(item['key'] for item in plan if registry.remove_if_owned(item['key'], item['manifest']))


class WindowsRegistry:
    """Only HKCU. Remove a key only when its sole value is our exact manifest."""
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Native host registration requires Windows.')
        import winreg
        self.api = winreg

    def get(self, key):
        try:
            with self.api.OpenKey(self.api.HKEY_CURRENT_USER, key) as handle:
                value, kind = self.api.QueryValueEx(handle, '')
                if kind != self.api.REG_SZ:
                    raise PermissionError('Preserve a native host registration of unknown type.')
                return value
        except FileNotFoundError:
            return None

    def set(self, key, value):
        with self.api.CreateKeyEx(self.api.HKEY_CURRENT_USER, key, 0, self.api.KEY_SET_VALUE) as handle:
            self.api.SetValueEx(handle, '', 0, self.api.REG_SZ, value)

    def remove_if_owned(self, key, value):
        if self.get(key) != value:
            return False
        with self.api.OpenKey(self.api.HKEY_CURRENT_USER, key) as handle:
            subkeys, values, _ = self.api.QueryInfoKey(handle)
            if subkeys or values != 1:
                return False
        self.api.DeleteKey(self.api.HKEY_CURRENT_USER, key)
        return True
