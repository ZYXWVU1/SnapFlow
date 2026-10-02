"""SDK OAuth adapters: OS-only secrets and a temporary loopback callback."""
import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit
from .transports import credential_id


def browser_url(value):
    """Browser URLs may carry OAuth state, but never embedded credentials."""
    from .models import validate_url
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ValueError('Unsupported authorization URL.')
    parsed = urlsplit(value)
    validate_url(parsed._replace(query='', fragment='').geturl())
    return value


async def enforce_http_request(request):
    browser_url(str(request.url))
    request.headers['Accept-Encoding'] = 'identity'


def oauth_provider(profile, credentials, callback, open_browser):
    from .diagnostics import install_safe_sdk_logging
    install_safe_sdk_logging()
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    class StoredExpiryProvider(OAuthClientProvider):
        async def _initialize(self):
            # SDK 2.2 loads tokens without restoring their expiry clock. Keep
            # refresh/grants in the SDK, and restore only its initialization data.
            try:
                await super()._initialize()
                await self._restore_expiry_metadata()
            except BaseException:
                self._initialized = False
                self.context.clear_tokens()
                self.context.client_info = None
                self.context.oauth_metadata = None
                self.context.auth_server_url = None
                raise

        async def _restore_expiry_metadata(self):
            token, info = self.context.current_tokens, self.context.client_info
            if token is not None:
                # Zero remaining lifetime must already be expired, including
                # on Windows clocks whose adjacent reads can be identical.
                expiry_token = token.model_copy(update={'expires_in': -1}) if token.expires_in == 0 else token
                self.context.update_token_expiry(expiry_token)
            if info is None or token is None:
                return
            if callback.redirect_uri not in tuple(str(uri) for uri in info.redirect_uris or ()):
                self.context.client_info = None  # SDK registers the replacement callback if needed.
                return
            if not info.issuer:
                self.context.clear_tokens()
                self.context.client_info = None
                return
            # A restored registration may belong to a separate authorization
            # server. Never refresh against the SDK's resource-origin fallback.
            import httpx2
            from .limits import bounded_http_response
            from mcp.client.auth.utils import (build_oauth_authorization_server_metadata_discovery_urls,
                handle_auth_metadata_response, validate_metadata_issuer)
            browser_url(info.issuer)
            urls = build_oauth_authorization_server_metadata_discovery_urls(info.issuer, profile.url)
            async with httpx2.AsyncClient(timeout=min(profile.timeout_seconds, 10), trust_env=False,
                    follow_redirects=False, event_hooks={'request': [enforce_http_request],
                        'response': [bounded_http_response]}) as client:
                for url in urls[:4]:
                    response = await client.get(url)
                    if len(response.content) > 65536:
                        raise ValueError('OAuth metadata exceeds its limit.')
                    ok, metadata = await handle_auth_metadata_response(response)
                    if not ok:
                        break
                    if metadata is not None:
                        validate_metadata_issuer(metadata, info.issuer)
                        for endpoint in (metadata.authorization_endpoint, metadata.token_endpoint,
                                metadata.registration_endpoint):
                            if endpoint is not None:
                                browser_url(str(endpoint))
                        self.context.oauth_metadata = metadata
                        self.context.auth_server_url = info.issuer
                        return
            raise PermissionError('OAuth issuer metadata unavailable. Reauthorize this connection.')
    async def redirect(url):
        browser_url(url)
        params = parse_qs(urlsplit(url).query, keep_blank_values=True)
        if len(params.get('state', [])) != 1:
            raise ValueError('OAuth authorization state is missing.')
        callback.expect(params['state'][0])
        if open_browser is None or not await open_browser(url):
            raise PermissionError('OAuth authorization cancelled.')
    return StoredExpiryProvider(server_url=profile.url,
        client_metadata=OAuthClientMetadata(client_name='SnapFlow',
            redirect_uris=[callback.redirect_uri], token_endpoint_auth_method='none'),
        storage=SecureOAuthStorage(credentials, profile.id, profile.url),
        redirect_handler=redirect, callback_handler=callback.wait)


class SecureOAuthStorage:
    """Chunk large SDK records within the existing Windows credential limit.

    An atomic manifest swap commits replacement only after all chunks are saved.
    Endpoint binding prevents reusing a profile ID against a different server.
    """
    def __init__(self, credentials, connection_id, endpoint):
        if credentials is None:
            raise PermissionError('Secure credential storage is required for OAuth.')
        self.credentials = credentials
        self.connection_id = connection_id
        self.binding = hashlib.sha256(endpoint.encode()).hexdigest()

    def _key(self, kind, suffix='manifest'):
        return credential_id(self.connection_id, f'oauth:{kind}:{suffix}')

    def _manifest(self, kind):
        raw = self.credentials.get(self._key(kind))
        if raw is None:
            return None
        try:
            if len(raw) > 300:
                raise ValueError()
            manifest = json.loads(raw)
            if (not re.fullmatch('[a-f0-9]{24}', manifest['revision']) or
                    type(manifest['count']) is not int or not 1 <= manifest['count'] <= 64 or
                    not re.fullmatch('[a-f0-9]{64}', manifest['hash']) or
                    not re.fullmatch('[a-f0-9]{64}', manifest['binding'])):
                raise ValueError()
            return manifest
        except (ValueError, KeyError, TypeError):
            raise ValueError('Stored OAuth record is invalid.') from None

    def _get(self, kind):
        manifest = self._manifest(kind)
        if manifest is None or manifest['binding'] != self.binding:
            return None
        chunks = [self.credentials.get(self._key(kind, f"{manifest['revision']}:{i}"))
            for i in range(manifest['count'])]
        if any(not isinstance(chunk, str) or len(chunk) > 1000 for chunk in chunks):
            raise ValueError('Stored OAuth record is incomplete.')
        raw = ''.join(chunks)
        if hashlib.sha256(raw.encode()).hexdigest() != manifest['hash']:
            raise ValueError('Stored OAuth record is invalid.')
        return json.loads(raw)

    def _delete_chunks(self, kind, manifest):
        if manifest:
            for i in range(manifest['count']):
                self.credentials.delete(self._key(kind, f"{manifest['revision']}:{i}"))

    def _set(self, kind, value):
        raw = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
        if len(raw) > 64000:
            raise ValueError('OAuth record exceeds secure storage limit.')
        old = self._manifest(kind)
        revision = secrets.token_hex(12)
        chunks = [raw[i:i + 1000] for i in range(0, len(raw), 1000)]
        manifest = {'revision': revision, 'count': len(chunks), 'hash': hashlib.sha256(raw.encode()).hexdigest(),
            'binding': self.binding}
        try:
            for i, chunk in enumerate(chunks):
                self.credentials.set(self._key(kind, f'{revision}:{i}'), chunk)
            self.credentials.set(self._key(kind), json.dumps(manifest, separators=(',', ':')))
        except Exception:
            self._delete_chunks(kind, manifest)
            raise
        self._delete_chunks(kind, old)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        record = self._get('tokens')
        if record is None:
            return None
        token = OAuthToken.model_validate(record['token'])
        if record['expires_at'] is not None:
            token.expires_in = max(0, math.floor(record['expires_at'] - time.time()))
        return token

    async def set_tokens(self, tokens):
        self._set('tokens', {'token': tokens.model_dump(mode='json'),
            'expires_at': time.time() + tokens.expires_in if tokens.expires_in is not None else None})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        record = self._get('client')
        return OAuthClientInformationFull.model_validate(record) if record is not None else None

    async def set_client_info(self, client_info):
        self._set('client', client_info.model_dump(mode='json'))

    async def clear(self):
        for kind in ('tokens', 'client'):
            manifest = self._manifest(kind)
            self.credentials.delete(self._key(kind))
            self._delete_chunks(kind, manifest)


class LoopbackCallback:
    def __init__(self, timeout=120, preferred_uri=None):
        self.timeout = timeout
        self.redirect_uri = ''
        self._state = None
        self._server = None
        self._clients = set()
        self.preferred_uri = preferred_uri

    @property
    def completed(self):
        return self._result.done()

    async def __aenter__(self):
        self._result = asyncio.get_running_loop().create_future()
        preferred = urlsplit(self.preferred_uri) if self.preferred_uri else None
        if preferred and (preferred.scheme != 'http' or preferred.hostname != '127.0.0.1' or
                not preferred.port or preferred.query or preferred.fragment or preferred.username or
                not re.fullmatch('/mcp/callback/[A-Za-z0-9_-]{24,64}', preferred.path)):
            preferred = None
        try:
            self._server = await asyncio.start_server(self._receive, '127.0.0.1', preferred.port if preferred else 0, limit=8192)
        except OSError:
            preferred = None
            self._server = await asyncio.start_server(self._receive, '127.0.0.1', 0, limit=8192)
        port = self._server.sockets[0].getsockname()[1]
        self.redirect_uri = self.preferred_uri if preferred else f'http://127.0.0.1:{port}/mcp/callback/{secrets.token_urlsafe(24)}'
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()
        for task in tuple(self._clients):
            task.cancel()
        await asyncio.gather(*self._clients, return_exceptions=True)
        if not self._result.done():
            self._result.cancel()
        elif not self._result.cancelled():
            self._result.exception()  # Consume an unobserved cancellation/error.

    def expect(self, state):
        if not isinstance(state, str) or not state or len(state) > 256:
            raise ValueError('Invalid OAuth state.')
        if self._result.done():
            if not self._result.cancelled():
                self._result.exception()
            self._result = asyncio.get_running_loop().create_future()
        self._state = state

    async def wait(self):
        return await asyncio.wait_for(asyncio.shield(self._result), self.timeout)

    async def _receive(self, reader, writer):
        task = asyncio.current_task()
        self._clients.add(task)
        ok = False
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(raw) > 8192:
                raise ValueError()
            lines = raw.decode('ascii').split('\r\n')
            method, target, version = lines[0].split(' ')
            headers = [line.partition(':') for line in lines[1:] if line]
            hosts = [value.strip() for key, separator, value in headers if key.lower() == 'host' and separator]
            expected = urlsplit(self.redirect_uri)
            parsed = urlsplit(target)
            params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
            if (method != 'GET' or version != 'HTTP/1.1' or parsed.scheme or parsed.netloc or
                    parsed.fragment or parsed.path != expected.path or hosts != [expected.netloc] or
                    self._result.done() or self._state is None or
                    any(len(value) != 1 for value in params.values()) or
                    not secrets.compare_digest(params.get('state', [''])[0], self._state)):
                raise ValueError()
            if 'error' in params:
                self._result.set_exception(PermissionError('OAuth authorization cancelled.'))
            elif params.get('code', [''])[0]:
                from mcp.client.auth import AuthorizationCodeResult
                self._result.set_result(AuthorizationCodeResult(code=params['code'][0],
                    state=params['state'][0], iss=params.get('iss', [None])[0]))
            else:
                raise ValueError()
            ok = True
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        finally:
            try:
                message = b'Authorization response received. Return to SnapFlow.' if ok else b'Invalid authorization response.'
                status = b'200 OK' if ok else b'400 Bad Request'
                writer.write(b'HTTP/1.1 ' + status + b'\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\n'
                    b'Connection: close\r\nContent-Length: ' + str(len(message)).encode() + b'\r\n\r\n' + message)
                await writer.drain()
            except (OSError, ConnectionError):
                pass
            writer.close()
            await writer.wait_closed()
            self._clients.discard(task)
