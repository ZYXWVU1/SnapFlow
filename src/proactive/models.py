from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class AssistanceSession:
    session_id: str
    started_at: float
    expires_at: float | None
    enabled_sources: tuple[str, ...]
    allowed_apps: tuple[str, ...] | None
    suggestion_level: str
    allow_fullscreen: bool


@dataclass(frozen=True)
class ProactiveSuggestion:
    suggestion_id: str
    type: str
    title: str
    description: str
    trigger: str
    source_ids: tuple[str, ...]
    goal: str
    proposed_capability: str | None
    risk_level: str
    expires_at: float
    dedup_key: str
