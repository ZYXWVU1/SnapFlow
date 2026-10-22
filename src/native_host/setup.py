"""Prepare/check/register a known native host; no extension installation."""
import argparse
import json
from pathlib import Path

from src.browser.registration import (WindowsRegistry, apply_registration, build_host_manifest,
    read_host_manifest, registration_plan, remove_registration)
from src.browser.protocol import HOST_NAME


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True, help='Absolute installed SnapFlowBrowserHost.exe')
    parser.add_argument('--chrome-id', action='append', default=[])
    parser.add_argument('--edge-id', action='append', default=[])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--register', action='store_true', help='Write per-user Chrome/Edge registrations')
    modes.add_argument('--unregister', action='store_true', help='Remove only matching owned registrations')
    modes.add_argument('--check', action='store_true', help='Check manifest and registry without writes')
    args = parser.parse_args(argv)
    manifest = build_host_manifest(args.host, chrome_ids=args.chrome_id, edge_ids=args.edge_id)
    target = args.host.with_name(HOST_NAME + '.json')
    if args.check or args.unregister:
        if read_host_manifest(target, args.host) != manifest:
            parser.error('Installed host manifest differs from supplied extension IDs.')
    else:
        # Do not replace a registered manifest or a changed allowlist silently.
        if target.exists():
            if read_host_manifest(target, args.host) != manifest:
                parser.error('Existing manifest differs. Review and remove its registration before replacing it.')
        else:
            with target.open('x', encoding='utf-8') as output:
                json.dump(manifest, output, indent=2)
    plan = registration_plan(target, chrome=bool(args.chrome_id), edge=bool(args.edge_id))
    if args.register:
        apply_registration(plan, WindowsRegistry())
    elif args.unregister:
        remove_registration(plan, WindowsRegistry())
    elif args.check:
        registry = WindowsRegistry()
        if any(registry.get(item['key']) != item['manifest'] for item in plan):
            parser.error('Native host registration is absent or points elsewhere.')
    print(json.dumps({'manifest': str(target), 'registrations': plan,
                     'registered': args.register, 'checked': args.check}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
