"""Request, session, source, and answer contracts for contextual questions."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


SCOPES = ('current_only', 'selected_memories', 'authorized_memory_search')


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ContextTurn:
    question: str
    answer: str
    source_ids: tuple[str, ...]
    memory_revisions: tuple[tuple[str, str], ...] = ()
    source_bindings: tuple[tuple[str, str, str], ...] = ()


@dataclass
class ContextSession:
    session_id: str
    screenshot_reference: str
    scope: str = 'current_only'
    selected_memory_ids: tuple[str, ...] = ()
    excluded_memory_ids: tuple[str, ...] = ()
    permission_version: int = 0
    created_at: str = field(default_factory=_now)
    history: list[ContextTurn] = field(default_factory=list)
    external_sources: tuple = ()
    external_prompt: str = ''

    @classmethod
    def new(cls, screenshot_reference: str):
        if not screenshot_reference:
            raise ValueError('A current screenshot reference is required.')
        return cls(uuid4().hex, screenshot_reference)


@dataclass(frozen=True)
class ContextRequest:
    request_id: str
    session_id: str
    screenshot_reference: str
    current_skill_result: dict | None
    user_question: str
    scope: str
    selected_memory_ids: tuple[str, ...]
    retrieval_enabled: bool
    conversation_enabled: bool
    permission_version: int
    created_at: str
    external_sources: tuple = ()
    external_prompt: str = ''

    @classmethod
    def new(cls, session: ContextSession, question: str, *, current_skill_result=None,
            scope=None, selected_memory_ids=None, conversation_enabled=True):
        chosen_scope = session.scope if scope is None else scope
        ids = session.selected_memory_ids if selected_memory_ids is None else tuple(selected_memory_ids)
        return cls(uuid4().hex, session.session_id, session.screenshot_reference,
            current_skill_result, question, chosen_scope, ids,
            chosen_scope == 'authorized_memory_search', conversation_enabled,
            session.permission_version, _now(), tuple(session.external_sources), session.external_prompt)


@dataclass(frozen=True)
class ContextSource:
    source_id: str
    source_type: str
    title: str
    content: str
    memory_id: str | None = None
    memory_revision: str | None = None
    screenshot_reference: str | None = None
    saved_at: str | None = None
    source_date: str | None = None
    relevance_score: float | None = None
    connection_id: str | None = None
    resource_uri: str | None = None
    retrieved_at: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class ContextBundle:
    prompt: str
    sources: tuple[ContextSource, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextualResult:
    request_id: str
    session_id: str
    answer: str
    source_ids: tuple[str, ...]
    sources: tuple[ContextSource, ...]
    suggestions: tuple = ()
    warnings: tuple[str, ...] = ()
    model_identifier: str | None = None
    created_at: str = field(default_factory=_now)
