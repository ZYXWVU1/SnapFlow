"""The only module that knows the AI provider's API format."""
import base64
import os

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

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
    def request_text(self, prompt: str, mode: str = 'optimizer') -> str:
        """Use the configured provider for an explicit text-only Skill proposal."""
        key = os.getenv('AI_API_KEY', '').strip()
        if not key:
            raise AnalysisError('AI API key is not configured. Open Settings to add your API key.')
        base_url = os.getenv('AI_BASE_URL') or 'https://api.openai.com/v1'
        model = os.getenv('AI_MODEL') or 'gpt-4.1-mini'
        try:
            with OpenAI(api_key=key, base_url=base_url,
                        timeout=_request_timeout(base_url), max_retries=0) as client:
                response = client.chat.completions.create(model=model,
                    messages=[{'role': 'user', 'content': prompt}],
                    max_tokens=_positive_int('AI_MAX_TOKENS', 1024, 128, 8192))
            if not response.choices or not response.choices[0].message.content:
                raise AnalysisError('The AI service returned an empty proposal.')
            return response.choices[0].message.content.strip()
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            raise AnalysisError('Unable to generate a Skill proposal with the configured AI service.') from None

    def analyze_image(self, image_bytes: bytes, mode: str = "ask", custom_prompt: str | None = None,
                      history: list[dict[str, str]] | None = None) -> str:
        prompt = get_prompt(mode)
        return self.request_image(image_bytes, prompt, mode, custom_prompt, history)

    def request_image(self, image_bytes: bytes, prompt: str, mode: str = '',
                      custom_prompt: str | None = None,
                      history: list[dict[str, str]] | None = None) -> str:
        """Shared vision transport for workflow and standalone classification prompts."""
        key = os.getenv("AI_API_KEY", "").strip()
        if not key:
            raise AnalysisError("AI API key is not configured. Open Settings to add your API key.")
        if not image_bytes:
            raise AnalysisError("The screenshot is empty. Please capture again.")
        if custom_prompt and custom_prompt.strip() and not history:
            prompt += "\n\nAdditional question about this image:\n" + custom_prompt.strip()
        base_url = os.getenv("AI_BASE_URL") or "https://api.openai.com/v1"
        model = os.getenv("AI_MODEL") or "gpt-4.1-mini"
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
        except APITimeoutError:
            raise AnalysisError(
                f"The AI request timed out after {_request_timeout(base_url):g} seconds. "
                "Try a faster model or increase AI_TIMEOUT in .env."
            ) from None
        except APIConnectionError:
            raise AnalysisError("Unable to contact the AI service. Check your internet connection and try again.") from None
        except APIStatusError as exc:
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
            raise AnalysisError("Unable to process the AI response. Check the configured model and endpoint.") from None
