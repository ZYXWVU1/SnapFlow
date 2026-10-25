"""Installed GUI entry point for explicit, ownership-preserving host setup."""
import argparse
import json
from pathlib import Path
import sys

from src.beta.flags import require_plain_path
from .protocol import HOST_NAME
from .registration import (WindowsRegistry, read_host_manifest, registration_plan,
                           remove_registration)


def unregister_owned(host, registry):
    """Read the installed exact allowlist, then remove only matching HKCU keys.

    Used before uninstall while the executable still exists. This never removes
    files or another installation's registration and does not grant permissions.
    """
    host = require_plain_path(Path(host).absolute())
    target = require_plain_path(host.with_name(HOST_NAME + '.json'))
    if not target.exists():
        return ()
    read_host_manifest(target, host)  # Malformed/mismatched ownership fails closed.
    return remove_registration(registration_plan(target, chrome=True, edge=True), registry)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--host', type=Path)
    parser.add_argument('--unregister-owned', action='store_true')
    known, remaining = parser.parse_known_args(args)
    host = known.host
    if host is None:
        if not getattr(sys, 'frozen', False):
            parser.error('Source setup requires --host with the installed absolute host path.')
        host = Path(sys.executable).resolve().with_name('SnapFlowBrowserHost.exe')
    require_plain_path(host)
    if known.unregister_owned:
        if remaining:
            parser.error('--unregister-owned does not accept registration modes or extension IDs.')
        removed = unregister_owned(host, WindowsRegistry())
        print(json.dumps({'removed_owned_registrations': list(removed)}))
        return 0
    from src.native_host.setup import main as existing_setup
    # Keep registration, repair, check, exact-ID validation and rollback in the
    # existing authority instead of maintaining a second allowlist implementation.
    forwarded = args if known.host is not None else ['--host', str(host), *args]
    return existing_setup(forwarded)


if __name__ == '__main__':
    raise SystemExit(main())
