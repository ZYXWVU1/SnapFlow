"""Bounded, proxy-free local OpenAI-compatible inference and embedding adapter."""
import base64
import math
import threading
import time
import httpx
import weakref
from openai import OpenAI

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
        with self._slots_lock:
            self._slot = self._slots.get(self.endpoint)
            if self._slot is None:
                self._slot = threading.BoundedSemaphore(1)
                self._slots[self.endpoint] = self._slot

    def _http(self):
        def guard(request):
            self.policy.require_allowed(str(request.url), 'local_ai' if self.local else 'cloud_ai')
        return httpx.Client(trust_env=False, follow_redirects=False,
                            event_hooks={'request': [guard]}, timeout=10)

    def _sdk(self, timeout):
        self.policy.require_allowed(self.endpoint, 'local_ai' if self.local else 'cloud_ai')
        return OpenAI(api_key=self._api_key, base_url=self.endpoint, max_retries=0,
                      timeout=timeout, http_client=self._http())

    def health_check(self):
        try:
            return self.model_id in self.list_models()
        except Exception:
            return False

    def list_models(self):
        with self._sdk(10) as client:
            return [model.id for model in client.models.list().data]

    def get_capabilities(self):
        return self.capabilities

    def shutdown(self):
        pass

    def _generate(self, request):
        from .cancellation import run_cancellable
        return run_cancellable(lambda: self._generate_blocking(request), request.cancelled, request.timeout_seconds)

    def _generate_blocking(self, request):
        if not self.local and request.privacy_requirement in ('local_only', 'private'):
            raise ValueError('This request requires inference on this machine.')
        if request.response_schema:
            if not self.capabilities.structured_output:
                raise ValueError('Structured output is not supported by this model.')
            schema_validator(request.response_schema)
        if request.cancelled and request.cancelled():
            raise ValueError('AI request cancelled.')
        if not self._slot.acquire(blocking=False):
            raise ValueError('Local AI is busy. Wait for the current request to finish.')
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
                image_bytes, metadata = prepare_image(request.image_bytes)
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
            with self._sdk(request.timeout_seconds) as client:
                response = client.chat.completions.create(**payload)
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

    def embed(self, texts):
        if not self.capabilities.embeddings:
            raise ValueError('Embeddings are unsupported by this model.')
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32 or any(not isinstance(text, str) or len(text) > 200000 for text in texts):
            raise ValueError('Invalid embedding batch.')
        if not self._slot.acquire(blocking=False):
            raise ValueError('Local AI is busy.')
        managed_active = False
        try:
            if self.manager:
                self.manager.mark_request_started()
                managed_active = True
            with self._sdk(120) as client:
                result = client.embeddings.create(model=self.model_id, input=texts)
            if sorted(item.index for item in result.data) != list(range(len(texts))):
                raise ValueError('The runtime returned missing or duplicate embedding indices.')
            vectors = [item.embedding for item in sorted(result.data, key=lambda item: item.index)]
            if len(vectors) != len(texts) or any(not vector or any(type(x) not in (float, int) or not math.isfinite(x) for x in vector) for vector in vectors):
                raise ValueError('The runtime returned invalid embedding vectors.')
            return vectors
        except ValueError:
            raise
        except Exception:
            raise ValueError('Local embedding generation is unavailable. Keyword search remains available.') from None
        finally:
            if managed_active:
                self.manager.mark_request_finished()
            self._slot.release()
