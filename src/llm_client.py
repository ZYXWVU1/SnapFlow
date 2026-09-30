"""The only module that knows the AI provider's API format."""
import base64
import logging
import os
import time

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
    def __init__(self, usage_callback=None, *, on_usage=None):
        self.usage_callback = usage_callback or on_usage

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

    def request_text(self, prompt: str, mode: str = 'optimizer', *, api_key=None,
                     base_url=None, model=None, usage_operation=None,
                     system_prompt=None) -> str:
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
                        timeout=_request_timeout(base_url), max_retries=0) as client:
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
        return self.request_image(image_bytes, prompt, mode, custom_prompt, history)

    def request_image(self, image_bytes: bytes, prompt: str, mode: str = '',
                      custom_prompt: str | None = None,
                      history: list[dict[str, str]] | None = None,
                      model_override: str | None = None,
                      usage_operation: str | None = None) -> str:
        """Shared vision transport for workflow and standalone classification prompts."""
        key = os.getenv("AI_API_KEY", "").strip()
        if not key:
            raise AnalysisError("AI API key is not configured. Open Settings to add your API key.")
        if not image_bytes:
            raise AnalysisError("The screenshot is empty. Please capture again.")
        if custom_prompt and custom_prompt.strip() and not history:
            prompt += "\n\nAdditional question about this image:\n" + custom_prompt.strip()
        base_url = os.getenv("AI_BASE_URL") or DEFAULT_AI_BASE_URL
        model = model_override.strip() if isinstance(model_override, str) else ""
        model = model or os.getenv("AI_MODEL") or DEFAULT_AI_MODEL
        if len(model) > 128:
            raise AnalysisError("The configured AI model identifier is too long.")
        is_siliconflow = "siliconflow.cn" in base_url.lower()
        image_url = {
            "url": "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
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
                        timeout=_request_timeout(base_url), max_retries=0) as client:
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
