import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from src.llm_client import AnalysisError, LLMClient


class ClientTests(unittest.TestCase):
    def test_missing_key(self):
        with patch.dict(os.environ, {"AI_API_KEY": ""}):
            with self.assertRaisesRegex(AnalysisError, "not configured"):
                LLMClient().analyze_image(b"image")

    @patch.dict(os.environ, {"AI_API_KEY": "test-placeholder"})
    @patch("src.llm_client.OpenAI")
    def test_image_payload_and_question(self, factory):
        client = factory.return_value.__enter__.return_value
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer"))])
        self.assertEqual(LLMClient().analyze_image(b"png", "debug", "Why?"), "Answer")
        content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Why?", content[1]["text"])
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64," + base64.b64encode(b"png").decode())
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)

    @patch.dict(os.environ, {
        "AI_API_KEY": "test-placeholder",
        "AI_BASE_URL": "https://api.siliconflow.cn/v1",
        "AI_MODEL": "Qwen/Qwen2.5-VL-72B-Instruct",
        "AI_TIMEOUT": "120",
        "AI_MAX_TOKENS": "1024",
        "AI_ENABLE_THINKING": "false",
    })
    @patch("src.llm_client.OpenAI")
    def test_siliconflow_payload_and_error_detail(self, factory):
        client = factory.return_value.__enter__.return_value
        request = httpx.Request("POST", "https://api.siliconflow.cn/v1/chat/completions")
        error = APIStatusError(
            "bad request",
            response=httpx.Response(400, request=request),
            body={"message": "model does not support image input"},
        )
        client.chat.completions.create.side_effect = error
        with self.assertRaisesRegex(AnalysisError, "model does not support image input"):
            LLMClient().analyze_image(b"png")
        content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(content[0]["image_url"]["detail"], "low")
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["max_tokens"], 1024)
        self.assertNotIn("extra_body", request)
        self.assertEqual(factory.call_args.kwargs["timeout"], 120.0)

    @patch.dict(os.environ, {"AI_API_KEY": "test-placeholder"})
    @patch("src.llm_client.OpenAI")
    def test_errors_are_readable_and_redacted(self, factory):
        request = httpx.Request("POST", "https://example.com")
        errors = [
            (APIConnectionError(request=request), "Unable to contact"),
            (APITimeoutError(request=request), "timed out"),
            (APIStatusError("secret payload", response=httpx.Response(401, request=request), body=None), "key was rejected"),
            (RuntimeError("secret payload"), "Unable to process"),
        ]
        for error, expected in errors:
            factory.return_value.__enter__.return_value.chat.completions.create.side_effect = error
            with self.assertRaisesRegex(AnalysisError, expected) as result:
                LLMClient().analyze_image(b"png")
            self.assertNotIn("secret payload", str(result.exception))
