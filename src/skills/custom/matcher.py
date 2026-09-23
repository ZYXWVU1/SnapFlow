"""One request for all enabled candidates; errors safely produce no match."""
from dataclasses import dataclass
import logging
from src.smart.models import valid_confidence
from .json_response import json_object
from .prompt_builder import matcher_prompt

CUSTOM_SKILL_MATCH_THRESHOLD = .75


@dataclass(frozen=True)
class CustomSkillMatchResult:
    skill_id: str | None = None
    confidence: float = 0.0
    failed: bool = False


class CustomSkillMatcher:
    def __init__(self, client):
        self.client = client

    def match(self, image_bytes, definitions):
        definitions = [s for s in definitions if s.enabled]
        if not definitions:
            return CustomSkillMatchResult()
        try:
            raw = json_object(self.client.request_image(image_bytes, matcher_prompt(definitions), mode='custom_match'))
            skill_id, confidence = raw.get('skill_id'), raw.get('confidence')
            if not valid_confidence(confidence) or (skill_id is not None and
                    (not isinstance(skill_id, str) or skill_id not in {s.id for s in definitions})):
                raise ValueError('Invalid match.')
            return CustomSkillMatchResult(skill_id if confidence >= CUSTOM_SKILL_MATCH_THRESHOLD else None, confidence)
        except Exception:
            logging.getLogger(__name__).warning('[CUSTOM_MATCH] Matching unavailable; falling back to Ask')
            return CustomSkillMatchResult(failed=True)
