"""Small allowlisted stdio relay. stdout contains only native protocol frames."""
import os
import sys

from src.browser.protocol import envelope, extension_origin, validate_envelope
from .protocol import read_message, write_message


def serve(caller_origin, allowed_origins, incoming, outgoing, exchange):
    try:
        extension_origin(caller_origin)
    except ValueError as exc:
        raise PermissionError('Native host caller is not approved.') from exc
    if caller_origin not in allowed_origins:
        raise PermissionError('Native host caller is not approved.')
    connection_id = None
    try:
        while True:
            message = read_message(incoming)
            if message is None:
                return
            request = validate_envelope(message)
            try:
                if ((connection_id is None and request['type'] != 'hello') or
                        (connection_id is not None and (request['type'] == 'hello' or request['connection_id'] != connection_id))):
                    raise PermissionError('Native host connection mismatch.')
                reply = validate_envelope(exchange(caller_origin, request), response=True)
                if reply['request_id'] != request['request_id']:
                    raise ValueError('Mismatched desktop response.')
                if reply['payload']['ok']:
                    if connection_id is not None and reply['connection_id'] != connection_id:
                        raise ValueError('Desktop connection identity changed.')
                    connection_id = reply['connection_id']
            except (PermissionError, ValueError, ConnectionError, OSError) as exc:
                code = 'permission' if isinstance(exc, PermissionError) else 'protocol' if isinstance(exc, ValueError) else 'unavailable'
                reply = envelope('result', {'ok': False, 'code': code},
                    connection_id=connection_id, session_id=request['session_id'], request_id=request['request_id'])
            write_message(outgoing, reply)
            if not reply['payload']['ok'] or request['type'] == 'disconnect':
                if request['type'] == 'disconnect' and reply['payload']['ok']:
                    connection_id = None
                return
    finally:
        if connection_id is not None:
            try:
                exchange(caller_origin, envelope('disconnect', {}, connection_id=connection_id))
            except Exception:
                pass  # Desktop lease expiry handles host/process loss; never replay writes.


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    stage = 'launch'
    try:
        if not args:
            raise PermissionError('Browser launch origin required.')
        # Load an installed manifest adjacent to the executable. Do not accept
        # identity/configuration from an extension's JSON payload or environment.
        from pathlib import Path
        stage = 'manifest'
        from src.browser.registration import read_host_manifest
        executable = Path(sys.executable).resolve()
        manifest = read_host_manifest(executable.parent / 'com.snapflow.browser.json', executable)
        if os.name == 'nt':
            stage = 'stdio'
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        stage = 'ipc_runtime'
        from .ipc_client import desktop_exchange
        from src.paths import AppPaths
        from src.integrations.credentials import CredentialService
        from PySide6.QtCore import QCoreApplication
        stage = 'application'
        app = QCoreApplication.instance() or QCoreApplication([])
        paths, credentials = AppPaths(), CredentialService()
        stage = 'relay'
        serve(args[0], manifest['allowed_origins'], sys.stdin.buffer, sys.stdout.buffer,
            lambda origin, message: desktop_exchange(paths, credentials, origin, message))
        return 0
    except Exception as exc:
        # Closed stage/class diagnostics contain no messages, paths, values or tokens.
        print(f'SnapFlow Browser Companion unavailable [{stage}/{type(exc).__name__}]. '
              'Check setup, approved extension IDs and desktop connection.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
