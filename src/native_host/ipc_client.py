"""Separate browser pipe and rotated OS credential; no HTTP/TCP listener."""
from src.mcp.server.ipc import call_ipc


def desktop_exchange(paths, credentials, caller_origin, message):
    return call_ipc(paths, credentials, 'exchange', {'origin': caller_origin, 'message': message},
                    namespace='browser', timeout=5)
