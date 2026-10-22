"""Explicit site preferences. A saved site grant never authorizes a live tab."""
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .models import origin
from .protocol import extension_origin


class BrowserSitePermissions:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock, self._allowed = RLock(), set()
        if self.path and self.path.exists():
            if self.path.stat().st_size > 65536:
                raise ValueError('Browser site preferences exceed limit.')
            value = json.loads(self.path.read_text(encoding='utf8'))
            if type(value) is not dict or set(value) != {'version', 'sites'} or value['version'] != 1 or type(value['sites']) is not list:
                raise ValueError('Invalid browser site preferences; original file preserved.')
            if len(value['sites']) > 100:
                raise ValueError('Too many browser site preferences.')
            for item in value['sites']:
                if type(item) is not dict or set(item) != {'extension_origin', 'origin'}:
                    raise ValueError('Invalid browser site preference.')
                extension_origin(item['extension_origin'])
                origin(item['origin'])
                self._allowed.add((item['extension_origin'], item['origin']))

    def allowed(self, extension, page_origin):
        with self.lock:
            return (extension, page_origin) in self._allowed

    def entries(self):
        with self.lock:
            return tuple(sorted(self._allowed))

    def set(self, extension, page_origin, allowed):
        extension_origin(extension)
        origin(page_origin)
        if type(allowed) is not bool:
            raise ValueError('Invalid site permission state.')
        with self.lock:
            updated = set(self._allowed)
            if allowed:
                updated.add((extension, page_origin))
            else:
                updated.discard((extension, page_origin))
            if len(updated) > 100:
                raise ValueError('Browser site permission limit reached.')
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
                try:
                    with temporary.open('x', encoding='utf8') as output:
                        json.dump({'version': 1, 'sites': [{'extension_origin': ext, 'origin': site}
                            for ext, site in sorted(updated)]}, output, indent=2)
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
            self._allowed = updated
