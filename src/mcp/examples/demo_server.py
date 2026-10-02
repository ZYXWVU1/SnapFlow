"""The specification's first client slice, with no SnapFlow service access."""
import json
import os
from mcp.server import MCPServer

server = MCPServer('SnapFlow development demo')


@server.tool()
def echo(message: str) -> dict[str, str]:
    """Return the supplied message without an external write."""
    return {'message': message}


@server.resource('demo://info', mime_type='application/json')
def demo_info() -> str:
    return json.dumps({'name': 'SnapFlow demo', 'credential_isolated': 'AI_API_KEY' not in os.environ})


@server.prompt()
def summarize_demo() -> str:
    return 'Summarize the explicitly selected demo resource and cite its source.'


if __name__ == '__main__':
    server.run()
