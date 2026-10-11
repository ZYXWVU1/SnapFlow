"""Data contracts and small capability interfaces for the existing worker architecture."""
from dataclasses import dataclass, field
from typing import Callable, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class ModelCapabilities:
    text: bool = True
    vision: bool = False
    embeddings: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    max_context_length: int | None = None
    modalities: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ('text', 'vision', 'embeddings', 'structured_output', 'tool_calling'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('Capabilities must be booleans.')
        if self.max_context_length is not None and (type(self.max_context_length) is not int or self.max_context_length < 1):
            raise ValueError('Context length must be a positive integer.')


@dataclass(frozen=True)
class AIRequest:
    prompt: str = field(default='', repr=False)
    task_type: str = 'text'
    request_id: str = field(default_factory=lambda: uuid4().hex)
    image_bytes: bytes | None = field(default=None, repr=False)
    conversation: tuple | list = field(default_factory=tuple, repr=False)
    response_schema: dict | None = field(default=None, repr=False)
    preferred_model: str | None = None
    privacy_requirement: str = 'standard'
    timeout_seconds: float = 120.0
    metadata: dict = field(default_factory=dict, repr=False)
    cancelled: Callable[[], bool] | None = field(default=None, repr=False, compare=False)
    before_inference: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        import math
        if not isinstance(self.prompt, str) or len(self.prompt) > 500000:
            raise ValueError('AI prompt is invalid or too large.')
        if self.privacy_requirement not in ('standard', 'local_only', 'private'):
            raise ValueError('Unknown privacy requirement.')
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 600:
            raise ValueError('AI timeout must be within 600 seconds.')
        if self.image_bytes is not None and (not isinstance(self.image_bytes, bytes) or len(self.image_bytes) > 32 * 1024 * 1024):
            raise ValueError('AI image is invalid or too large.')


@dataclass(frozen=True)
class AIResponse:
    request_id: str
    runtime_id: str
    model_id: str
    content: str | None = field(default=None, repr=False)
    structured_data: dict | None = field(default=None, repr=False)
    usage: dict | None = None
    latency_ms: float = 0.0
    local: bool = False
    warnings: tuple[str, ...] = ()
    image_metadata: dict | None = None


@dataclass(frozen=True)
class RoutingDecision:
    request_id: str
    runtime_id: str
    model_id: str
    reason_codes: tuple[str, ...]
    local: bool
    fallback_allowed: bool = False
    estimated_quality: float | None = None
    estimated_latency_ms: float | None = None
    estimated_cost: float | None = None


class AIRuntime(Protocol):
    runtime_id: str
    model_id: str
    local: bool
    capabilities: ModelCapabilities

    def health_check(self) -> bool: ...
    def list_models(self) -> list: ...
    def get_capabilities(self) -> ModelCapabilities: ...
    def shutdown(self) -> None: ...


class TextGenerationRuntime(Protocol):
    def generate_text(self, request: AIRequest) -> AIResponse: ...


class VisionRuntime(Protocol):
    def analyze_image(self, request: AIRequest) -> AIResponse: ...


class EmbeddingRuntime(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
