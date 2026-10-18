"""Bounded, proxy-free local OpenAI-compatible inference and embedding adapter."""
import base64
import math
import threading
import time
import httpx
import weakref
from contextlib import contextmanager

from src.network_policy import get_network_policy, is_loopback_url
from .models import AIResponse
from .images import prepare_image
from .validation import schema_validator, validate_schema


class LocalOpenAICompatibleRuntime:
    runtime_id = 'local_openai'
    _slots = weakref.WeakValueDictionary()
    _slots_lock = threading.Lock()

    def __init__(self, endpoint, model_id, capabilities, policy=None, *, allow_remote=False, api_key=None, manager=None):
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid local runtime endpoint.')
        self.local = is_loopback_url(endpoint)
        if not self.local and not allow_remote:
            raise ValueError('Local AI requires a loopback endpoint. Remote endpoints transmit content off this machine.')
        self.endpoint, self.model_id = endpoint.rstrip('/'), model_id
        self.capabilities, self.policy = capabilities, policy or get_network_policy()
        self._api_key = api_key or 'local-runtime'
        self.manager = manager
        from .transport import ClientPool
        self._clients = ClientPool(capacity=1)
        self._health_lock = threading.Lock()
        self._healthy_until = 0.0
        self._closed = False
        self._request_scope = threading.local()
        with self._slots_lock:
            self._slot = self._slots.get(self.endpoint)
            if self._slot is None:
                self._slot = threading.BoundedSemaphore(1)
                self._slots[self.endpoint] = self._slot

    def _http(self):
        from src.ai.deadline_transport import DeadlineTransport
        def guard(request):
            with self._health_lock:
                if self._closed:
                    raise ValueError('Local AI runtime is stopped.')
            deadline = getattr(self._request_scope, 'deadline', None)
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError('AI request timed out.')
            cancelled = getattr(self._request_scope, 'cancelled', None)
            if cancelled and cancelled():
                raise ValueError('AI request cancelled.')
            self.policy.require_allowed(str(request.url), 'local_ai' if self.local else 'cloud_ai')
        transport = DeadlineTransport(scope=lambda: (
            getattr(self._request_scope, 'deadline', None),
            getattr(self._request_scope, 'cancelled', None)),
            trust_env=False, max_connections=2)
        return httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                            event_hooks={'request': [guard]}, timeout=10)

    @contextmanager
    def _sdk(self, timeout, *, deadline=None, cancelled=None):
        deadline = deadline if deadline is not None else time.monotonic() + timeout
        if timeout <= 0:
            raise ValueError('AI request timed out.')
        self.policy.require_allowed(self.endpoint, 'local_ai' if self.local else 'cloud_ai')
        def factory():
            from openai import OpenAI
            http = self._http()
            try:
                return OpenAI(api_key=self._api_key, base_url=self.endpoint, max_retries=0,
                              timeout=timeout, http_client=http)
            except Exception:
                http.close()
                raise
        previous_deadline = getattr(self._request_scope, 'deadline', None)
        previous_cancelled = getattr(self._request_scope, 'cancelled', None)
        self._request_scope.deadline, self._request_scope.cancelled = deadline, cancelled
        try:
            with self._clients.lease((self.endpoint, self._api_key), factory) as client:
                remaining = min(timeout, deadline - time.monotonic())
                if remaining <= 0:
                    raise ValueError('AI request timed out.')
                yield client.with_options(timeout=remaining)
        finally:
            self._request_scope.deadline, self._request_scope.cancelled = previous_deadline, previous_cancelled

    def health_check_request(self, request):
        return self.health_check(timeout=min(10, request.remaining_timeout),
            deadline=min(request.deadline, time.monotonic() + 10), cancelled=request.cancelled)

    def health_check(self, *, timeout=10, deadline=None, cancelled=None):
        self.policy.require_allowed(self.endpoint, 'local_ai' if self.local else 'cloud_ai')
        with self._health_lock:
            if self._closed:
                return False
            if time.monotonic() < self._healthy_until:
                return True
        try:
            return self.model_id in self.list_models(timeout=timeout, deadline=deadline, cancelled=cancelled)
        except Exception:
            return False

    def list_models(self, *, timeout=10, deadline=None, cancelled=None):
        try:
            with self._sdk(timeout, deadline=deadline, cancelled=cancelled) as client:
                models = [model.id for model in client.models.list().data]
        except Exception:
            with self._health_lock:
                self._healthy_until = 0.0
            raise
        with self._health_lock:
            if self._closed:
                raise ValueError('Local AI runtime is stopped.')
            self._healthy_until = time.monotonic() + 5 if self.model_id in models else 0.0
        return models

    def get_capabilities(self):
        return self.capabilities

    def shutdown(self):
        with self._health_lock:
            self._closed = True
            self._healthy_until = 0.0
        self._clients.close()

    def _generate(self, request):
        from .cancellation import run_cancellable
        return run_cancellable(lambda: self._generate_blocking(request), request.cancelled, request.remaining_timeout,
                               background=request.metadata.get('operation') == 'evaluation')

    def _generate_blocking(self, request):
        if not self.local and request.privacy_requirement in ('local_only', 'private'):
            raise ValueError('This request requires inference on this machine.')
        if request.response_schema:
            if not self.capabilities.structured_output:
                raise ValueError('Structured output is not supported by this model.')
            schema_validator(request.response_schema)
        if request.cancelled and request.cancelled():
            raise ValueError('AI request cancelled.')
        # A dispatched background inference cannot be preempted. Let an
        # interactive request wait for that slot under its original deadline;
        # admission keeps subsequent background batches behind this request.
        while not self._slot.acquire(timeout=min(.02, request.remaining_timeout)):
            if request.cancelled and request.cancelled():
                raise ValueError('AI request cancelled.')
            if request.remaining_timeout <= 0:
                raise ValueError('AI request timed out.')
        started = time.perf_counter()
        metadata = None
        managed_active = False
        try:
            if self.manager:
                self.manager.mark_request_started()
                managed_active = True
            messages = []
            if request.metadata.get('system_prompt'):
                messages.append({'role': 'system', 'content': request.metadata['system_prompt']})
            prompt = request.prompt
            question = request.metadata.get('custom_prompt')
            mode = request.metadata.get('mode')
            if request.image_bytes:
                if not self.capabilities.vision:
                    raise ValueError('The local model has no verified Vision support. Open AI & Models.')
                prepare = request.image_scope.prepare if request.image_scope is not None else prepare_image
                image_bytes, metadata = prepare(request.image_bytes)
                if question and not request.conversation:
                    prompt += '\n\nAdditional question about this image:\n' + question.strip()
                content = [{'type': 'image_url', 'image_url': {'url':
                    'data:image/' + metadata['encoding'] + ';base64,' + base64.b64encode(image_bytes).decode('ascii')}},
                    {'type': 'text', 'text': prompt}]
            else:
                content = prompt
            messages.append({'role': 'user', 'content': content})
            if request.conversation and mode == 'ask':
                messages.extend(dict(item) for item in request.conversation[-12:])
                messages.append({'role': 'user', 'content': (question or '').strip() or 'Explain this screenshot.'})
            payload = dict(model=self.model_id, messages=messages,
                           max_tokens=request.metadata.get('max_tokens', 1024))
            if request.response_schema:
                payload['response_format'] = {'type': 'json_schema', 'json_schema': {
                    'name': 'snapflow_result', 'schema': request.response_schema}}
            with self._sdk(request.remaining_timeout, deadline=request.deadline, cancelled=request.cancelled) as client:
                from .streaming import complete
                response = complete(client, payload, request.stream, cancelled=request.cancelled,
                    deadline=request.deadline, guard=lambda: self.policy.require_allowed(
                        self.endpoint, 'local_ai' if self.local else 'cloud_ai'))
            if request.cancelled and request.cancelled():
                raise ValueError('AI request cancelled.')
            if not response.choices or not isinstance(response.choices[0].message.content, str) or not response.choices[0].message.content.strip():
                raise ValueError('The local model returned an empty response.')
            if request.task_type == 'structured_extraction' and response.choices[0].finish_reason == 'length':
                raise ValueError('The local structured response was truncated. Increase the output limit or capture a smaller region.')
            usage = getattr(response, 'usage', None)
            counts = None if usage is None else dict(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
            text = response.choices[0].message.content.strip()
            structured = validate_schema(request.response_schema, text) if request.response_schema else None
            return AIResponse(request.request_id, self.runtime_id, self.model_id,
                text, structured_data=structured, usage=counts,
                latency_ms=(time.perf_counter() - started) * 1000, local=self.local, image_metadata=metadata)
        except ValueError:
            raise
        except Exception:
            with self._health_lock:
                self._healthy_until = 0.0
            raise ValueError('Local AI could not complete this request. Check runtime health, model support and available memory in AI & Models.') from None
        finally:
            if managed_active:
                self.manager.mark_request_finished()
            self._slot.release()

    def generate_text(self, request):
        if not self.capabilities.text:
            raise ValueError('Text generation is unsupported by this model.')
        return self._generate(request)

    def analyze_image(self, request):
        return self._generate(request)

    def embed(self, texts, *, cancelled=None, deadline=None):
        if not self.capabilities.embeddings:
            raise ValueError('Embeddings are unsupported by this model.')
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32 or any(not isinstance(text, str) or len(text) > 200000 for text in texts):
            raise ValueError('Invalid embedding batch.')
        deadline = time.monotonic() + 120 if deadline is None else deadline
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError('Invalid embedding request deadline.')
        def check_budget():
            if cancelled and cancelled():
                raise ValueError('AI request cancelled.')
            if time.monotonic() >= deadline:
                raise ValueError('AI request timed out.')
        check_budget()
        while not self._slot.acquire(timeout=min(.02, max(0, deadline - time.monotonic()))):
            check_budget()
        managed_active = False
        try:
            if self.manager:
                self.manager.mark_request_started()
                managed_active = True
            check_budget()
            with self._sdk(min(120, deadline - time.monotonic()), deadline=deadline, cancelled=cancelled) as client:
                result = client.embeddings.create(model=self.model_id, input=texts)
            check_budget()
            if sorted(item.index for item in result.data) != list(range(len(texts))):
                raise ValueError('The runtime returned missing or duplicate embedding indices.')
            vectors = [item.embedding for item in sorted(result.data, key=lambda item: item.index)]
            if len(vectors) != len(texts) or any(not vector or any(type(x) not in (float, int) or not math.isfinite(x) for x in vector) for vector in vectors):
                raise ValueError('The runtime returned invalid embedding vectors.')
            return vectors
        except ValueError:
            raise
        except Exception:
            check_budget()
            with self._health_lock:
                self._healthy_until = 0.0
            raise ValueError('Local embedding generation is unavailable. Keyword search remains available.') from None
        finally:
            if managed_active:
                self.manager.mark_request_finished()
            self._slot.release()
