"""SDK transport construction. No shells, inherited secrets or stderr logs."""
from contextlib import asynccontextmanager
import hashlib
import os

from src.network_policy import get_network_policy


def credential_id(connection_id, purpose='bearer'):
    return 'mcp_' + hashlib.sha256(f'{connection_id}:{purpose}'.encode()).hexdigest()[:40]


def make_transport(profile, credentials, *, open_browser=None):
    get_network_policy().require_allowed('stdio' if profile.transport == 'stdio' else profile.url,
        'mcp_stdio' if profile.transport == 'stdio' else 'mcp_remote')
    from mcp import StdioServerParameters, stdio_client
    if profile.transport == 'stdio':
        environment = {}
        for key in profile.secret_environment:
            value = credentials.get(credential_id(profile.id, key)) if credentials else None
            if not value:
                raise ValueError('A configured environment credential is missing.')
            environment[key] = value
        # errlog belongs to the transport context, not global application logs.
        @asynccontextmanager
        async def stdio():
            from .limits import STDIO_ENCODING
            with open(os.devnull, 'w', encoding='utf-8') as errlog:
                params = StdioServerParameters(command=profile.executable, args=list(profile.args),
                    cwd=profile.working_directory, env=environment,
                    encoding=STDIO_ENCODING)
                async with stdio_client(params, errlog=errlog) as streams:
                    yield streams
        return stdio()
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    headers = {}
    if profile.auth_mode == 'bearer':
        token = credentials.get(credential_id(profile.id)) if credentials else None
        if not token:
            raise PermissionError('Authentication is required.')
        headers['Authorization'] = 'Bearer ' + token
    @asynccontextmanager
    async def http():
        get_network_policy().require_allowed(profile.url, 'mcp_remote')
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            auth = None
            if profile.auth_mode == 'oauth':
                from .oauth import LoopbackCallback, SecureOAuthStorage, oauth_provider
                saved = await SecureOAuthStorage(credentials, profile.id, profile.url).get_client_info()
                preferred = str(saved.redirect_uris[0]) if saved and saved.redirect_uris else None
                callback = await stack.enter_async_context(LoopbackCallback(preferred_uri=preferred))
                auth = oauth_provider(profile, credentials, callback, open_browser)
            from .oauth import enforce_http_request
            from .limits import bounded_http_response
            transport_client = await stack.enter_async_context(httpx2.AsyncClient(headers=headers, auth=auth,
                timeout=profile.timeout_seconds,
                # httpx2 counts auth subrequests against this bound as well.
                follow_redirects=False, max_redirects=20 if auth else 3, trust_env=False,
                event_hooks={'request': [enforce_http_request], 'response': [bounded_http_response]}))
            streams = await stack.enter_async_context(streamable_http_client(profile.url, http_client=transport_client))
            yield streams
    return http()
