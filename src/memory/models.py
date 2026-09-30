"""Stable values returned by the local Memory service."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    title: str
    description: str
    source_type: str
    skill_id: str | None
    skill_version_id: str | None
    structured_data: dict
    searchable_text: str
    screenshot_reference: str | None
    thumbnail_reference: str | None
    tags: tuple[str, ...]
    source_created_at: str | None
    saved_at: str
    updated_at: str
    content_hash: str | None
    schema_version: int = 1


@dataclass(frozen=True)
class MemorySearchResult:
    memory_id: str
    title: str
    snippet: str
    saved_at: str
    skill_name: str | None
    thumbnail_reference: str | None
    relevance_score: float | None
