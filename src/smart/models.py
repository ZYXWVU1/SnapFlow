"""Validated classifier data shared by classification and routing."""
from dataclasses import dataclass
import math
from typing import Any

SUPPORTED_CONTENT_TYPES = frozenset({'assignment', 'event', 'code_error', 'table', 'unknown'})


def valid_confidence(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


@dataclass(frozen=True)
class ClassificationResult:
    content_type: str
    confidence: float
    reasoning: str | None = None
    metadata: dict[str, Any] | None = None

    def is_valid(self) -> bool:
        return (isinstance(self.content_type, str) and self.content_type in SUPPORTED_CONTENT_TYPES
                and valid_confidence(self.confidence)
                and (self.reasoning is None or isinstance(self.reasoning, str))
                and (self.metadata is None or isinstance(self.metadata, dict)))


def unknown(reason: str) -> ClassificationResult:
    return ClassificationResult('unknown', 0.0, reason)
