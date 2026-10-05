"""The only module that knows the AI provider's API format."""
import base64
import logging
import os
import time
import threading
import httpx

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from src.config import DEFAULT_AI_BASE_URL, DEFAULT_AI_MODEL
from src.prompts import get_prompt


class AnalysisError(Exception):
    """A message safe to display without exposing request data or credentials."""


def _status_error_detail(error: APIStatusError) -> str:
    """Extract the provider's safe error message without exposing request data."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        message = body.get("message")
        if not message and isinstance(body.get("error"), dict):
            message = body["error"].get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:600]
    if isinstance(body, str) and body.strip():
        return body.strip()[:600]
    return ""


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _request_timeout(base_url: str) -> float:
    default = 120.0 if "siliconflow.cn" in base_url.lower() else 45.0
    try:
        value = float(os.getenv("AI_TIMEOUT", str(default)).strip())
    except ValueError:
        return default
    return max(10.0, min(value, 600.0))


class LLMClient:
    def __init__(self, usage_callback=None, *, on_usage=None, config=None, policy=None,
                 runtime_choice=None, routing_callback=None, performance_store=None, paths=None):
        self.usage_callback = usage_callback or on_usage
        from src.config import Config
        from src.network_policy import get_network_policy
        self.config = config or Config()
        self.policy = policy or get_network_policy()
        self.runtime_choice = runtime_choice
        self.routing_callback = routing_callback
        self.performance_store = performance_store
        from src.paths import AppPaths
        self.paths = paths or AppPaths()
        self.last_response = None
        self.last_decision = None
        self._local_runtime = None
        self.managed_runtime = None
        self.fallback_callback = None
        self._request_scope = threading.local()

    def scoped(self, cancelled):
        client = self
        class ScopedClient:
            @property
            def cancellation_callback(self):
                return cancelled

            def __setattr__(self, name, value):
                setattr(client, name, value)

            def __getattr__(self, name):
                value = getattr(client, name)
                if name not in ('request_text', 'request_image', 'request_validated_image', 'analyze_image', 'test_connection'):
                    return value
                def call(*args, **kwargs):
                    previous = getattr(client._request_scope, 'cancelled', None)
                    client._request_scope.cancelled = cancelled
                    try:
                        return value(*args, **kwargs)
                    finally:
                        client._request_scope.cancelled = previous
                return call
        return ScopedClient()

    def configure(self, config):
        self.config = config
        self._local_runtime = None

    def routing_metadata(self, skill):
        definition = getattr(skill, 'definition', None)
        return {'skill_id': skill.id, 'definition_snapshot': definition.to_dict() if definition else None}

    def _execute(self, request, cloud_executor, *, cloud_model=None, cloud_endpoint=None, validator=None):
        from src.ai.cloud import CloudRuntime
        from src.ai.local import LocalOpenAICompatibleRuntime
        from src.ai.models import ModelCapabilities
        from src.ai.router import AIRuntimeRouter
        from dataclasses import replace
        cloud_model = cloud_model or self.config.ai_model or os.getenv('AI_MODEL') or DEFAULT_AI_MODEL
        cloud_endpoint = cloud_endpoint or self.config.ai_base_url or os.getenv('AI_BASE_URL') or DEFAULT_AI_BASE_URL
        runtimes = [CloudRuntime(cloud_model, cloud_executor, cloud_endpoint, self.policy)]
        config = self.config
        from src.ai.routing_context import collect_resources, RoutingContextResolver
        resources = collect_resources() if config.local_ai_model else {}
        data = {**request.metadata, **resources, 'cloud_base_url': cloud_endpoint}
        if data.get('definition_snapshot') and self.performance_store:
            evidence = RoutingContextResolver(self.paths, self.performance_store).resolve(
                data['skill_id'], data['definition_snapshot'], local_model_id=config.local_ai_model,
                cloud_model_id=cloud_model, local_model_version=config.local_ai_model_version,
                cloud_model_version=config.ai_model_version, hardware_profile_id=data.get('hardware_profile_id'))
            data.update(evidence)
        data.pop('definition_snapshot', None)
        request = replace(request, metadata=data)
        if config.local_ai_model:
            if self._local_runtime is None:
                caps = ModelCapabilities(text=True, vision=config.local_ai_vision,
                    structured_output=config.local_ai_structured_output,
                    max_context_length=config.local_ai_context_length)
                self._local_runtime = LocalOpenAICompatibleRuntime(config.local_ai_endpoint,
                    config.local_ai_model, caps, self.policy, api_key=self.managed_runtime.api_key
                    if self.managed_runtime and self.managed_runtime.endpoint == config.local_ai_endpoint.rstrip('/') else None,
                    manager=self.managed_runtime if self.managed_runtime and self.managed_runtime.endpoint == config.local_ai_endpoint.rstrip('/') else None)
            runtimes.append(self._local_runtime)
            try:
                from src.ai.local_models import LocalModelManager, ModelManagementError
                record = LocalModelManager(self.paths).get_model(config.local_ai_model)
                self._local_runtime.expected_ram_mb = record.expected_ram_mb
            except (ValueError, OSError, ModelManagementError):
                self._local_runtime.expected_ram_mb = None
        if config.ai_execution_mode == 'ask_every_time' and not (config.private_mode or self.policy.private_mode):
            choice = request.metadata.get('runtime_choice') or (self.runtime_choice(request) if self.runtime_choice else None)
            request = replace(request, metadata={**request.metadata, 'runtime_choice': choice})
        router = AIRuntimeRouter(runtimes, config, self.policy,
            performance_store=self.performance_store, fallback_callback=self.fallback_callback,
            config_provider=lambda: self.config)
        try:
            response = router.execute(request, validator)
            if self.config is not config:
                raise AnalysisError('AI settings changed during this request. Retry using the current policy.')
            decision = router.last_decision
            self.last_response, self.last_decision = response, decision
            if response.local and self.usage_callback:
                from src.usage import UsageEvent
                counts = response.usage or {}
                self.usage_callback(UsageEvent(config.local_ai_endpoint, response.model_id,
                    request.metadata.get('operation', 'local_ai'), counts.get('input_tokens', 0),
                    counts.get('output_tokens', 0), latency_ms=round(response.latency_ms),
                    usage_available=response.usage is not None, local=True))
            if self.routing_callback:
                self.routing_callback(response, decision)
            return response.content
        except AnalysisError:
            raise
        except ValueError as exc:
            from src.modes import ResponseFormatError
            if isinstance(exc, ResponseFormatError):
                raise
            raise AnalysisError(str(exc)) from None

    def request_validated_image(self, image_bytes, prompt, validator, *, mode='skill', **kwargs):
        return self.request_image(image_bytes, prompt, mode=mode, validator=validator, **kwargs)

    def _cloud_http(self, transport=None, cancelled=None):
        def guard(request):
            if cancelled and cancelled():
                raise AnalysisError('AI request cancelled.')
            if self.config.private_mode or self.config.ai_execution_mode == 'local_only':
                raise AnalysisError('The current policy blocks cloud AI.')
            self.policy.require_allowed(str(request.url), 'cloud_ai')
        return httpx.Client(transport=transport, follow_redirects=False,
                            event_hooks={'request': [guard]})

    def request_text(self, prompt: str, mode: str = 'optimizer', *, api_key=None,
                     base_url=None, model=None, usage_operation=None,
                     system_prompt=None, privacy_requirement='standard', validator=None,
                     runtime_choice=None, metadata=None, cancelled=None) -> str:
        from src.ai.models import AIRequest
        from src.observability.performance import emit
        prepared = time.perf_counter()
        request = AIRequest(prompt=prompt, task_type='contextual_reasoning' if mode == 'context' else 'text',
            cancelled=cancelled or getattr(self._request_scope, 'cancelled', None),
            preferred_model=model, privacy_requirement=privacy_requirement,
            metadata={**(metadata or {}), **dict(mode=mode, system_prompt=system_prompt, operation=usage_operation or 'text',
                          max_tokens=_positive_int('AI_MAX_TOKENS', 1024, 128, 8192), runtime_choice=runtime_choice)})
        emit('operation_completed', component='ai_router', duration_ms=(time.perf_counter() - prepared) * 1000,
             success=True, properties={'operation': 'request_prepare'})
        return self._execute(request, lambda req: self._cloud_request_text(req.prompt, mode,
            api_key=api_key, base_url=req.metadata['cloud_base_url'], model=req.preferred_model, usage_operation=usage_operation,
            system_prompt=system_prompt, cancelled=req.cancelled), cloud_model=model, cloud_endpoint=base_url, validator=validator)

    def request_image(self, image_bytes: bytes, prompt: str, mode: str = '',
                      custom_prompt=None, history=None, model_override=None, usage_operation=None,
                      *, response_schema=None, validator=None, privacy_requirement='standard',
                      runtime_choice=None, metadata=None, cancelled=None) -> str:
        from src.ai.models import AIRequest
        from src.observability.performance import emit
        prepared = time.perf_counter()
        if not image_bytes:
            raise AnalysisError('The screenshot is empty. Please capture again.')
        request = AIRequest(prompt=prompt, task_type='structured_extraction' if mode in ('skill', 'extract', 'debug') else 'vision',
            cancelled=cancelled or getattr(self._request_scope, 'cancelled', None),
            image_bytes=image_bytes, conversation=history or (), response_schema=response_schema,
            preferred_model=model_override, privacy_requirement=privacy_requirement,
            metadata={**(metadata or {}), **dict(mode=mode, custom_prompt=custom_prompt, operation=usage_operation or mode or 'image',
                          max_tokens=_positive_int('AI_MAX_TOKENS', 1024, 128, 8192), runtime_choice=runtime_choice,
                          cloud_max_image_width=self.config.max_image_width)})
        emit('operation_completed', component='ai_router', duration_ms=(time.perf_counter() - prepared) * 1000,
             success=True, properties={'operation': 'request_prepare'})
        return self._execute(request, lambda req: self._cloud_request_image(req.image_bytes, req.prompt, mode,
            custom_prompt, history, req.preferred_model, usage_operation, cancelled=req.cancelled,
            base_url=req.metadata['cloud_base_url'],
            image_encoding=req.metadata.get('image_encoding', 'png')), cloud_model=model_override, validator=validator)

    def _record_usage(self, response, *, model, base_url, operation, started=None, failure=None):
        if self.usage_callback is None:
            return
        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'prompt_tokens', None)
        output_tokens = getattr(usage, 'completion_tokens', None)
        usage_available = type(input_tokens) is int and type(output_tokens) is int
        if not usage_available:
            input_tokens = output_tokens = 0
        details = getattr(usage, 'prompt_tokens_details', None)
        cached_tokens = getattr(details, 'cached_tokens', 0) or 0
        if type(cached_tokens) is not int or cached_tokens < 0:
            cached_tokens = 0
        cached_tokens = min(input_tokens, cached_tokens)
        failure_category = None
        if failure is not None:
            from openai import APIConnectionError, APIStatusError, APITimeoutError
            if isinstance(failure, APITimeoutError):
                failure_category = 'timeout'
            elif isinstance(failure, APIConnectionError):
                failure_category = 'connection'
            elif isinstance(failure, APIStatusError):
                status = failure.status_code
                failure_category = f'http_{status}' if status in (400, 401, 403, 404, 429) else 'http_other'
            else:
                failure_category = 'request_error'
        try:
            from src.usage import UsageEvent
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000)) if started is not None else 0
            self.usage_callback(UsageEvent(
                base_url, model, operation, input_tokens, output_tokens, cached_tokens,
                latency_ms=elapsed_ms, succeeded=failure is None,
                failure_category=failure_category, usage_available=usage_available))
        except Exception:
            logging.getLogger(__name__).warning('Unable to record provider token usage.')

    def _cloud_request_text(self, prompt: str, mode: str = 'optimizer', *, api_key=None,
                     base_url=None, model=None, usage_operation=None,
                     system_prompt=None, cancelled=None) -> str:
        """Use the configured provider for an explicit text-only Skill proposal."""
        key = (os.getenv('AI_API_KEY', '') if api_key is None else api_key).strip()
        if not key:
            raise AnalysisError('AI API key is not configured. Open Settings to add your API key.')
        base_url = base_url or os.getenv('AI_BASE_URL') or DEFAULT_AI_BASE_URL
        model = model or os.getenv('AI_MODEL') or DEFAULT_AI_MODEL
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 128:
            raise AnalysisError('The configured AI model identifier is invalid.')
        model = model.strip()
        started = time.perf_counter()
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})
        try:
            with OpenAI(api_key=key, base_url=base_url,
                        timeout=_request_timeout(base_url), max_retries=0,
                        http_client=self._cloud_http(cancelled=cancelled)) as client:
                response = client.chat.completions.create(model=model,
                    messages=messages,
                    max_tokens=_positive_int('AI_MAX_TOKENS', 1024, 128, 8192))
            self._record_usage(response, model=model, base_url=base_url,
                               operation=usage_operation or 'text', started=started)
            if not response.choices or not response.choices[0].message.content:
                raise AnalysisError('The AI service returned an empty proposal.')
            return response.choices[0].message.content.strip()
        except APITimeoutError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or 'text', started=started, failure=exc)
            raise AnalysisError('The AI connection test timed out. Check the endpoint and try again.') from None
        except APIConnectionError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or 'text', started=started, failure=exc)
            raise AnalysisError('Unable to contact the AI service. Check the endpoint and internet connection.') from None
        except APIStatusError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or 'text', started=started, failure=exc)
            messages = {
                400: 'The AI service rejected the request. Check the selected model.',
                401: 'The AI API key was rejected. Check the key and provider account.',
                403: 'Access denied. Check the provider account and model permissions.',
                404: 'The AI model or endpoint was not found. Check the endpoint and model.',
                429: 'The provider rate limit or quota was reached. Check the provider account.',
            }
            message = messages.get(exc.status_code, f'AI service error (HTTP {exc.status_code}).')
            raise AnalysisError(message) from None

    def test_connection(self, *, api_key: str, base_url: str, model: str) -> bool:
        """Send one explicit, short text request to verify compatible chat access."""
        self.request_text(
            'Reply with OK to confirm the connection.',
            api_key=api_key,
            base_url=base_url,
            model=model,
            usage_operation='connection_test',
        )
        return True

    def analyze_image(self, image_bytes: bytes, mode: str = "ask", custom_prompt: str | None = None,
                      history: list[dict[str, str]] | None = None) -> str:
        prompt = get_prompt(mode)
        from src.modes import parse_result
        validator = (lambda text: parse_result(mode, text)) if mode in ('debug', 'extract') else None
        return self.request_image(image_bytes, prompt, mode, custom_prompt, history, validator=validator)

    def _cloud_request_image(self, image_bytes: bytes, prompt: str, mode: str = '',
                      custom_prompt: str | None = None,
                      history: list[dict[str, str]] | None = None,
                      model_override: str | None = None,
                      usage_operation: str | None = None, *, cancelled=None, image_encoding='png', base_url=None) -> str:
        """Shared vision transport for workflow and standalone classification prompts."""
        key = os.getenv("AI_API_KEY", "").strip()
        if not key:
            raise AnalysisError("AI API key is not configured. Open Settings to add your API key.")
        if not image_bytes:
            raise AnalysisError("The screenshot is empty. Please capture again.")
        if custom_prompt and custom_prompt.strip() and not history:
            prompt += "\n\nAdditional question about this image:\n" + custom_prompt.strip()
        base_url = base_url or os.getenv("AI_BASE_URL") or DEFAULT_AI_BASE_URL
        model = model_override.strip() if isinstance(model_override, str) else ""
        model = model or os.getenv("AI_MODEL") or DEFAULT_AI_MODEL
        if len(model) > 128:
            raise AnalysisError("The configured AI model identifier is too long.")
        is_siliconflow = "siliconflow.cn" in base_url.lower()
        image_url = {
            "url": "data:image/" + image_encoding + ";base64," + base64.b64encode(image_bytes).decode("ascii"),
        }
        # SiliconFlow documents image-first content and supports detail=low/high/auto.
        # Low detail is a safer default for screenshots and reduces visual token use.
        if is_siliconflow:
            image_url["detail"] = os.getenv("AI_IMAGE_DETAIL", "low").strip() or "low"
        content = [
            {"type": "image_url", "image_url": image_url},
            {"type": "text", "text": prompt},
        ]
        messages = [{"role": "user", "content": content}]
        if history and mode == 'ask':
            messages.extend(dict(message) for message in history[-12:])
            messages.append({'role': 'user', 'content': (custom_prompt or '').strip() or 'Explain this screenshot.'})
        started = time.perf_counter()
        try:
            with OpenAI(api_key=key, base_url=base_url,
                        timeout=_request_timeout(base_url), max_retries=0,
                        http_client=self._cloud_http(cancelled=cancelled)) as client:
                request = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": _positive_int("AI_MAX_TOKENS", 1024, 128, 8192),
                }
                if is_siliconflow:
                    thinking = os.getenv("AI_ENABLE_THINKING", "").strip().lower()
                    # Some SiliconFlow vision models reject this parameter even
                    # when it is false. Only send it when explicitly enabled.
                    if thinking == "true":
                        request["extra_body"] = {"enable_thinking": True}
                response = client.chat.completions.create(**request)
            self._record_usage(response, model=model, base_url=base_url,
                               operation=usage_operation or mode or 'image', started=started)
            if not response.choices:
                raise AnalysisError("The AI service returned no answer. Please try again.")
            if mode in ('extract', 'debug', 'ocr', 'skill') and getattr(response.choices[0], 'finish_reason', None) == 'length':
                raise AnalysisError(
                    'The AI response was cut off by the output token limit. '
                    'Capture a smaller region or increase AI_MAX_TOKENS in .env (for example, 4096), then restart the app.'
                )
            message = response.choices[0].message
            text = message.content or getattr(message, "refusal", None)
            if not isinstance(text, str) or not text.strip():
                raise AnalysisError("The AI service returned an empty answer. Please try again.")
            return text.strip()
        except APITimeoutError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or mode or 'image', started=started, failure=exc)
            raise AnalysisError(
                f"The AI request timed out after {_request_timeout(base_url):g} seconds. "
                "Try a faster model or increase AI_TIMEOUT in .env."
            ) from None
        except APIConnectionError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or mode or 'image', started=started, failure=exc)
            raise AnalysisError("Unable to contact the AI service. Check your internet connection and try again.") from None
        except APIStatusError as exc:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or mode or 'image', started=started, failure=exc)
            messages = {
                400: "The AI service rejected the request (HTTP 400). Check that AI_MODEL is a vision model and that the image format is supported.",
                401: "The AI API key was rejected. Check your key in Settings.",
                403: "Access denied. Check your API account and model permissions.",
                404: "AI model or endpoint not found. Check AI_MODEL and AI_BASE_URL in .env.",
                429: "AI rate limit or quota reached. Check your API account and try again later.",
            }
            message = messages.get(exc.status_code, f"AI service error (HTTP {exc.status_code}). Please try again.")
            detail = _status_error_detail(exc)
            if detail:
                message += f" Details: {detail}"
            raise AnalysisError(message) from None
        except AnalysisError:
            raise
        except Exception:
            self._record_usage(None, model=model, base_url=base_url,
                               operation=usage_operation or mode or 'image', started=started,
                               failure=RuntimeError())
            raise AnalysisError("Unable to process the AI response. Check the configured model and endpoint.") from None
