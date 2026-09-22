"""One visual request, safely parsed; failures degrade to Ask routing."""
import json
import logging
import re

from .models import ClassificationResult, unknown
from .prompts import CLASSIFICATION_PROMPT

logger = logging.getLogger(__name__)


def parse_classification_response(raw: str) -> ClassificationResult:
    try:
        raw = raw.strip()
        fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\s*```', raw, re.DOTALL | re.IGNORECASE)
        if fence:
            raw = fence.group(1)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('Expected object')
        result = ClassificationResult(data['content_type'], data['confidence'],
                                      data.get('reasoning'), data.get('metadata'))
        if not result.is_valid():
            raise ValueError('Invalid classification')
        return result
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        logger.warning('[SMART] Classification parse failed; falling back to Ask')
        return unknown('Unable to parse classifier response.')


class ScreenshotClassifier:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def classify(self, image_bytes: bytes) -> ClassificationResult:
        logger.info('[SMART] Classification started')
        try:
            raw = self.llm_client.request_image(image_bytes, CLASSIFICATION_PROMPT)
            result = parse_classification_response(raw)
        except Exception:
            # Provider exception strings may contain private request data.
            logger.warning('[SMART] Classification request failed; falling back to Ask')
            result = unknown('Classification unavailable.')
        logger.info('[SMART] Type: %s; Confidence: %.2f', result.content_type, result.confidence)
        return result
