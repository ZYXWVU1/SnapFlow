"""The only mapping from validated content types to executable workflows."""
import logging
from src.config import SMART_CLASSIFICATION_THRESHOLD
from .models import ClassificationResult, valid_confidence
from src.skills.registry import SKILLS

logger = logging.getLogger(__name__)


class SmartRouter:

    def __init__(self, threshold: float = SMART_CLASSIFICATION_THRESHOLD):
        if not valid_confidence(threshold):
            raise ValueError('Smart classification threshold must be between 0 and 1.')
        self.threshold = threshold

    def route(self, result: ClassificationResult) -> str:
        route = 'ask'
        if isinstance(result, ClassificationResult) and result.is_valid() and result.confidence >= self.threshold:
            route = result.content_type if result.content_type in SKILLS else 'ask'
        logger.info('[SMART] Route: %s', route)
        return route
