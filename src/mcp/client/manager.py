"""One reusable async runtime; each SDK lifecycle stays in its owning task."""
import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from dataclasses import replace
import threading
import time

from src.context.models import ContextSource
from src.mcp.identity import SelfConnectionError, instance_marker
from src.mcp.permissions.service import MCPPermissionService
from .discovery import bounded_json, discover
from .models import CapabilitySnapshot, ConnectionState, InvocationResult, now
from .transports import credential_id, make_transport


class MCPClientManager:
    def __init__(self, storage, credentials=None, *, on_event=None, transport_factory=None,
            open_browser=None, interaction_handler=None):
        self.storage, self.credentials = storage, credentials
        self.instance_marker = instance_marker(storage.path.parent)
        from .diagnostics import install_safe_sdk_logging
        install_safe_sdk_logging()
        self.permissions = MCPPermissionService(storage)
        self.on_event = on_event
        self.open_browser = open_browser
        self.interaction_handler = interaction_handler
        self.transport_factory = transport_factory or (lambda p, c: make_transport(p, c,
            open_browser=lambda url: self._authorize(p, url)))
        self._states, self._snapshots, self._connections = {}, {}, {}
        self._active_operations = {}
        self._listening, self._dirty = set(), set()
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._closed = False

    @property
    def runtime_alive(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def listening_connections(self):
        with self._lock:
            return frozenset(self._listening)

    def _changed(self, connection_id, queue):
        if connection_id in self._dirty:
            return
        self._dirty.add(connection_id)
        with self._lock:
            self._snapshots[connection_id] = CapabilitySnapshot()
        self._state(connection_id, status='permission_review_required')
        response = self._loop.create_future()
        response.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        try:
            queue.put_nowait(('refresh', (), response))
        except asyncio.QueueFull:
            response.cancel()  # A later explicit refresh/invocation will rediscover.

    async def _message(self, connection_id, queue, message):
        if getattr(message, 'method', '') in ('notifications/tools/list_changed',
                'notifications/resources/list_changed', 'notifications/prompts/list_changed'):
            self._changed(connection_id, queue)

    async def _listen(self, connection_id, client, queue):
        try:
            async with client.listen(tools_list_changed=True, resources_list_changed=True,
                    prompts_list_changed=True) as subscription:
                with self._lock:
                    self._listening.add(connection_id)
                async for _ in subscription:
                    self._changed(connection_id, queue)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Subscription support is optional; invocation still refreshes metadata.
            pass
        finally:
            with self._lock:
                self._listening.discard(connection_id)

    @staticmethod
    async def _stop_listener(task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _submit(self, coroutine):
        with self._lock:
            if self._closed:
                coroutine.close()
                raise RuntimeError('MCP manager is closed.')
            if not self.runtime_alive:
                ready = threading.Event()
                def run():
                    self._loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._loop)
                    ready.set()
                    self._loop.run_forever()
                    self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                    self._loop.close()
                self._thread = threading.Thread(target=run, name='SnapFlow-MCP', daemon=True)
                self._thread.start()
                ready.wait(5)
            return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def state(self, connection_id):
        with self._lock:
            return self._states.get(connection_id, ConnectionState(connection_id))

    def snapshot(self, connection_id):
        with self._lock:
            return deepcopy(self._snapshots.get(connection_id, CapabilitySnapshot()))

    def _state(self, connection_id, **kwargs):
        with self._lock:
            state = replace(self.state(connection_id), **kwargs)
            self._states[connection_id] = state
        if self.on_event:
            self.on_event(state)
        return state

    def connect(self, connection_id):
        return self._submit(self._connect(connection_id))

    async def _authorize(self, profile, url):
        previous = self.state(profile.id).status
        self._state(profile.id, status='needs_authentication')
        if (self.open_browser is None or self.storage.get_profile(profile.id) != profile or
                not await self.open_browser(profile.id, url)):
            raise PermissionError('OAuth authorization cancelled.')
        if self.storage.get_profile(profile.id) != profile:
            raise PermissionError('Connection changed during authorization.')
        self._state(profile.id, status=previous if previous in ('connected', 'permission_review_required',
            'awaiting_user_input') else 'connecting')
        return True

    async def _elicit(self, profile, context, params):
        from mcp.types import ElicitResult
        from .interactions import normalize_request, validate_response
        active = self._active_operations.get(profile.id)
        if self.interaction_handler is None or active is None or active['operation'] not in ('tool', 'resource', 'resource_template', 'prompt'):
            return ElicitResult(action='cancel')
        active['interactions'] += 1
        if active['interactions'] > 3:
            return ElicitResult(action='cancel')
        def authorized():
            if (profile.id in self._dirty or self.storage.get_profile(profile.id) != profile or
                    (active['guard'] and not active['guard']())):
                return False
            if active['operation'] == 'tool':
                return self.permissions.permits(profile.id, active['target'], active['fingerprint'],
                    approved=active['approved'])
            return True
        if not authorized():
            return ElicitResult(action='cancel')
        previous = self.state(profile.id).status
        try:
            request = normalize_request(profile.id, active['operation'], active['target'], params)
            self._state(profile.id, status='awaiting_user_input')
            response = await asyncio.wait_for(self.interaction_handler(request), 60)
            return validate_response(request, response) if authorized() else ElicitResult(action='cancel')
        except (ValueError, TypeError, TimeoutError):
            return ElicitResult(action='cancel')
        finally:
            if self.state(profile.id).status == 'awaiting_user_input':
                self._state(profile.id, status=previous)

    async def _connect(self, connection_id):
        profile = self.storage.get_profile(connection_id)
        if profile is None or not profile.enabled:
            raise ValueError('Enable this profile before connecting.')
        if connection_id in self._connections:
            return self.state(connection_id)
        if len(self._connections) >= 4:
            raise ValueError('At most four MCP servers can be connected.')
        ready = self._loop.create_future()
        queue = asyncio.Queue(maxsize=16)
        task = self._loop.create_task(self._own_connection(profile, queue, ready))
        self._connections[connection_id] = (task, queue)
        try:
            return await ready
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _own_connection(self, profile, queue, ready):
        connection_id = profile.id
        self._state(connection_id, status='connecting', error='')
        active_response = None
        try:
            from mcp import Client
            transport = self.transport_factory(profile, self.credentials)
            # Enter/operate/exit SDK and anyio scopes in this same task.
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(120 if profile.auth_mode == 'oauth' else profile.timeout_seconds):
                    client = await stack.enter_async_context(Client(transport,
                        read_timeout_seconds=profile.timeout_seconds, input_required_max_rounds=3,
                        elicitation_callback=lambda context, params: self._elicit(profile, context, params),
                        message_handler=lambda message: self._message(connection_id, queue, message)))
                    if client.instructions == self.instance_marker:
                        raise SelfConnectionError()
                    snapshot = await discover(connection_id, client)
                self.storage.reconcile_policies(connection_id, {t.name: t.fingerprint for t in snapshot.tools})
                with self._lock:
                    self._snapshots[connection_id] = snapshot
                if client.protocol_version == '2026-07-28':
                    listener = self._loop.create_task(self._listen(connection_id, client, queue))
                    stack.push_async_callback(self._stop_listener, listener)
                info = client.server_info
                state = self._state(connection_id, status='permission_review_required' if snapshot.tools else 'connected',
                    server_name=(info.name if info else '')[:256], server_version=(info.version if info else '')[:128],
                    protocol_version=client.protocol_version,
                    capabilities=tuple(name for name in ('tools', 'resources', 'prompts')
                        if getattr(client.server_capabilities, name, None) is not None), connected_at=now())
                if not ready.done():
                    ready.set_result(state)
                while True:
                    operation, args, active_response = await queue.get()
                    if operation == 'disconnect':
                        break
                    if active_response.cancelled():
                        continue
                    try:
                        self._active_operations[connection_id] = {'operation': operation,
                            'target': args[0] if args else '', 'interactions': 0,
                            'guard': args[4] if operation == 'tool' else None,
                            'approved': args[2] if operation == 'tool' else False,
                            'fingerprint': args[3] if operation == 'tool' else None}
                        async with asyncio.timeout(max(120, profile.timeout_seconds) if self.interaction_handler else profile.timeout_seconds):
                            result = await self._operate(profile, client, operation, args)
                        if not active_response.done():
                            active_response.set_result(result)
                    except Exception as exc:
                        if not active_response.done():
                            active_response.set_exception(ValueError(self._safe_error(exc)))
                    finally:
                        self._active_operations.pop(connection_id, None)
                    active_response = None
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
        except Exception as exc:
            auth_error = isinstance(exc, PermissionError) or type(exc).__name__ in (
                'OAuthFlowError', 'OAuthTokenError', 'OAuthRegistrationError')
            self._state(connection_id, status='needs_authentication' if auth_error and profile.auth_mode != 'none' else 'error',
                error=self._safe_error(exc))
            if not ready.done():
                ready.set_exception(ValueError(self._safe_error(exc)))
        finally:
            with self._lock:
                self._snapshots.pop(connection_id, None)
            self._connections.pop(connection_id, None)
            self._active_operations.pop(connection_id, None)
            self._dirty.discard(connection_id)
            if self.state(connection_id).status not in ('error', 'needs_authentication'):
                self._state(connection_id, status='disconnected')
            if active_response is not None and not active_response.done():
                active_response.set_exception(ValueError('MCP connection closed.'))
            while not queue.empty():
                _, _, response = queue.get_nowait()
                if not response.done():
                    response.set_exception(ValueError('MCP connection closed.'))

    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, SelfConnectionError) or (isinstance(exc, BaseExceptionGroup) and
                exc.subgroup(SelfConnectionError) is not None):
            return 'This MCP server appears to be this SnapFlow instance. Connection blocked.'
        from .limits import WireLimitError
        if isinstance(exc, WireLimitError) or (isinstance(exc, BaseExceptionGroup) and
                exc.subgroup(WireLimitError) is not None):
            return 'MCP response exceeds a transport limit or uses unsupported compression.'
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return 'MCP operation timed out.'
        # Exceptions can include endpoint query, server messages or credential values.
        return f'MCP operation failed ({type(exc).__name__}). Check the connection and configuration.'

    async def _request(self, connection_id, operation, *args):
        entry = self._connections.get(connection_id)
        if entry is None or self.state(connection_id).status not in ('connected', 'permission_review_required', 'awaiting_user_input'):
            raise ValueError('MCP server is unavailable. Connect it first.')
        response = self._loop.create_future()
        entry[1].put_nowait((operation, args, response))
        try:
            return await response
        except asyncio.CancelledError:
            # Cancellation tears down the session; mutating calls are never replayed.
            entry[0].cancel()
            raise

    async def _operate(self, profile, client, operation, args):
        if self.storage.get_profile(profile.id) != profile:
            raise ValueError('Profile changed. Reconnect before using capabilities.')
        snapshot = self.snapshot(profile.id)
        if operation == 'refresh':
            updated = await discover(profile.id, client)
            self.storage.reconcile_policies(profile.id, {t.name: t.fingerprint for t in updated.tools})
            with self._lock:
                self._snapshots[profile.id] = updated
            self._state(profile.id, status='permission_review_required' if updated.tools else 'connected')
            self._dirty.discard(profile.id)
            return updated
        if operation == 'tool':
            from jsonschema import Draft202012Validator
            # The SDK's response cache may be invalidated by a notification while
            # our normalized snapshot remains unchanged. Rediscover before every
            # invocation so stale local review cannot authorize a changed tool.
            current_snapshot = await discover(profile.id, client)
            self._dirty.discard(profile.id)
            self.storage.reconcile_policies(profile.id, {t.name: t.fingerprint for t in current_snapshot.tools})
            if current_snapshot != snapshot:
                with self._lock:
                    self._snapshots[profile.id] = current_snapshot
                self._state(profile.id, status='permission_review_required')
            snapshot = current_snapshot
            name, arguments, approved, expected_fingerprint, execution_guard = args
            tool = next((t for t in snapshot.tools if t.name == name), None)
            if tool is None:
                return InvocationResult(False, 'Tool unavailable.', category='unavailable')
            if expected_fingerprint is not None and expected_fingerprint != tool.fingerprint:
                return InvocationResult(False, 'Tool schema changed. Review this step.', category='schema_changed')
            if not self.permissions.permits(profile.id, name, tool.fingerprint, approved=approved):
                return InvocationResult(False, 'Tool permission rejected.', category='permission_rejected')
            try:
                bounded_json(arguments, 65536)
                Draft202012Validator(tool.input_schema).validate(arguments)
            except Exception:
                return InvocationResult(False, 'Tool arguments do not match its schema.', category='schema_validation')
            start = time.monotonic()
            if self.storage.get_profile(profile.id) != profile:
                return InvocationResult(False, 'Connection profile changed.', category='unavailable')
            if execution_guard is not None and not execution_guard():
                return InvocationResult(False, 'Execution cancelled or Workflow changed.', category='cancelled')
            try:
                raw = await client.call_tool(name, arguments, read_timeout_seconds=profile.timeout_seconds)
            except Exception as exc:
                return InvocationResult(False, self._safe_error(exc), category='timeout' if isinstance(exc, TimeoutError) else 'transport',
                    duration_seconds=time.monotonic() - start)
            text_parts, total, truncated = [], 0, False
            for content in raw.content[:64]:
                text = content.text if content.type == 'text' else f'[{content.type} content omitted]'
                remaining = max(0, 16000 - total)
                text_parts.append(text[:remaining])
                total += min(len(text), remaining)
                truncated |= len(text) > remaining
            structured = raw.structured_content
            try:
                bounded_json(structured)
            except ValueError:
                structured, truncated = None, True
            return InvocationResult(not raw.is_error, '\n'.join(text_parts), structured,
                'tool_error' if raw.is_error else '', time.monotonic() - start, truncated or len(raw.content) > 64)
        if operation in ('resource', 'resource_template'):
            if operation == 'resource_template':
                from mcp.shared.uri_template import UriTemplate
                template, values = args
                metadata = next((r for r in snapshot.resource_templates if r['uri_template'] == template), None)
                if metadata is None:
                    raise ValueError('Choose a discovered resource template.')
                parsed = UriTemplate.parse(template)
                if (not isinstance(values, dict) or len(values) > 16 or
                        any(not isinstance(v, str) or len(v) > 1024 for v in values.values()) or
                        set(values) - set(parsed.variable_names) or
                        not set(parsed.variable_names) - set(parsed.query_variable_names) <= set(values)):
                    raise ValueError('Resource template parameters do not match.')
                bounded_json(values, 16000)
                uri = parsed.expand(values)
                if len(uri) > 2048 or parsed.match(uri) is None:
                    raise ValueError('Resource template could not be expanded safely.')
            else:
                uri = args[0]
                metadata = next((r for r in snapshot.resources if r['uri'] == uri), None)
            if metadata is None:
                raise ValueError('Choose a discovered resource.')
            raw = await client.read_resource(uri)
            chunks = []
            for item in raw.contents:
                if (not hasattr(item, 'text') or str(item.uri) != uri or
                        item.mime_type not in (None, 'text/plain', 'text/markdown', 'application/json')):
                    raise ValueError('Resource content or MIME type is unsupported.')
                chunks.append(item.text)
                if sum(len(s.encode('utf-8')) for s in chunks) > 65536:
                    raise ValueError('Resource content exceeds 64 KiB.')
            return ContextSource('MCP1', 'mcp_resource', metadata.get('title') or metadata.get('name') or uri,
                '\n'.join(chunks), connection_id=profile.id, resource_uri=uri, retrieved_at=now(),
                mime_type=metadata.get('mime_type'))
        if operation == 'prompt':
            name, arguments = args
            descriptor = next((p for p in snapshot.prompts if p['name'] == name), None)
            if descriptor is None or not isinstance(arguments, dict) or any(not isinstance(v, str) for v in arguments.values()):
                raise ValueError('Choose a discovered prompt with text arguments.')
            bounded_json(arguments, 16000)
            allowed = {a['name'] for a in descriptor.get('arguments') or ()}
            required = {a['name'] for a in descriptor.get('arguments') or () if a.get('required')}
            if set(arguments) - allowed or not required <= set(arguments):
                raise ValueError('Prompt arguments do not match the discovered prompt.')
            raw = await client.get_prompt(name, arguments)
            texts = []
            for message in raw.messages:
                if message.content.type != 'text':
                    raise ValueError('Only text prompts are supported.')
                texts.append(message.content.text)
                if sum(len(t.encode('utf-8')) for t in texts) > 16000:
                    raise ValueError('Prompt exceeds its size limit.')
            return '\n'.join(texts)
        raise ValueError('Unsupported MCP operation.')

    def call_tool(self, connection_id, name, arguments, *, approved=False, expected_fingerprint=None, execution_guard=None):
        if expected_fingerprint is None:
            previous = next((t for t in self.snapshot(connection_id).tools if t.name == name), None)
            expected_fingerprint = previous.fingerprint if previous else None
        return self._submit(self._request(connection_id, 'tool', name, deepcopy(arguments), approved, expected_fingerprint, execution_guard))

    def read_resource(self, connection_id, uri):
        return self._submit(self._request(connection_id, 'resource', uri))

    def read_resource_template(self, connection_id, template, parameters):
        return self._submit(self._request(connection_id, 'resource_template', template, deepcopy(parameters)))

    def get_prompt(self, connection_id, name, arguments):
        return self._submit(self._request(connection_id, 'prompt', name, deepcopy(arguments)))

    def refresh(self, connection_id):
        return self._submit(self._request(connection_id, 'refresh'))

    def disconnect(self, connection_id):
        async def disconnect():
            await self._disconnect(connection_id)
            profile = self.storage.get_profile(connection_id)
            if profile and profile.auth_mode == 'oauth':
                await self._forget(profile)
        return self._submit(disconnect())

    async def _disconnect(self, connection_id):
        entry = self._connections.get(connection_id)
        if entry:
            entry[0].cancel()
            await asyncio.gather(entry[0], return_exceptions=True)
        return self.state(connection_id)

    def remove_profile(self, connection_id):
        async def remove():
            await self._disconnect(connection_id)
            profile = self.storage.get_profile(connection_id)
            await self._forget(profile)
            self.storage.delete_profile(connection_id)
        return self._submit(remove())

    async def _forget(self, profile):
        if self.credentials and profile:
            from .oauth import SecureOAuthStorage
            await SecureOAuthStorage(self.credentials, profile.id, profile.url or 'stdio').clear()
            for purpose in ('bearer', *profile.secret_environment):
                self.credentials.delete(credential_id(profile.id, purpose))

    def forget_credentials(self, connection_id):
        async def forget():
            await self._disconnect(connection_id)
            await self._forget(self.storage.get_profile(connection_id))
        return self._submit(forget())

    def close(self):
        if self._closed:
            return
        if self.runtime_alive:
            async def shutdown():
                for connection_id in tuple(self._connections):
                    await self._disconnect(connection_id)
            self._submit(shutdown()).result(15)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(5)
        self._closed = True
