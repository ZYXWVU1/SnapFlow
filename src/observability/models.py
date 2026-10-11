"""Small operational records with per-launch random identifiers."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AppEvent:
    event_type: str
    category: str | None = None
    component: str | None = None
    duration_ms: float | None = None
    success: bool | None = None
    properties: Mapping = field(default_factory=dict)
    privacy_class: str = 'safe_metadata'
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=now)
    session_id: str | None = None


@dataclass(frozen=True)
class AppSession:
    session_id: str
    started_at: str
    app_version: str
    release_channel: str
    private_mode: bool
    previous_session_unclean: bool
