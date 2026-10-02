"""Opaque local-instance marker for recursion detection, never authorization."""
import hashlib
from pathlib import Path


class SelfConnectionError(ValueError):
    pass


def instance_marker(data_dir):
    digest = hashlib.sha256(str(Path(data_dir).resolve()).casefold().encode('utf-8')).hexdigest()
    return 'snapflow-instance:v1:' + digest
