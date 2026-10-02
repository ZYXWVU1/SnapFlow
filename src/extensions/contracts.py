"""Explicit interfaces for application-maintainer-owned native adapters."""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from threading import Event
from typing import Callable, Iterable, Protocol
from src.context.models import ContextSource
from src.skill_actions import ActionDefinition
from src.skills.base import Skill


@dataclass(frozen=True)
class PermissionMetadata:
    id: str
    risk: str
    capabilities: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', self.id):
            raise ValueError('Invalid provider identifier.')
        if self.risk not in ('read_only', 'clipboard', 'local_write', 'external_write', 'sensitive', 'destructive', 'unknown'):
            raise ValueError('Invalid risk declaration.')
        if not isinstance(self.capabilities, (tuple, list)) or not 1 <= len(self.capabilities) <= 16 or any(
                not isinstance(item, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', item) for item in self.capabilities):
            raise ValueError('Invalid capability declaration.')
        object.__setattr__(self, 'capabilities', tuple(self.capabilities))


class ActionProvider(Protocol):
    metadata: PermissionMetadata
    def actions(self) -> Iterable[ActionDefinition]: ...


class SkillProvider(Protocol):
    metadata: PermissionMetadata
    def skills(self) -> Iterable[Skill]: ...


class ContextProvider(Protocol):
    metadata: PermissionMetadata
    async def retrieve(self, selection: str, cancel: Event) -> ContextSource: ...


class MCPAdapter(Protocol):
    def connect(self, connection_id: str): ...
    def disconnect(self, connection_id: str): ...
    def call_tool(self, connection_id: str, name: str, arguments: dict, **guards): ...
    def read_resource(self, connection_id: str, uri: str): ...


async def retrieve_selected_context(provider: ContextProvider, selection: str, *,
        cancel: Event, permission_check: Callable[[], bool]) -> ContextSource:
    """Retrieve one explicitly selected native source without granting Memory access."""
    metadata = provider.metadata
    if not isinstance(metadata, PermissionMetadata) or metadata.risk != 'read_only' or 'selected_context' not in metadata.capabilities:
        raise PermissionError('Provider cannot contribute read-only selected context.')
    if not isinstance(selection, str) or len(selection) > 2048 or not selection.startswith(f'extension://{metadata.id}/'):
        raise ValueError('Selection must identify this provider resource.')
    def current():
        return not cancel.is_set() and permission_check()
    if not current():
        raise PermissionError('Selected context permission unavailable.')
    task = asyncio.create_task(provider.retrieve(selection, cancel))
    try:
        deadline = asyncio.get_running_loop().time() + 30
        while not task.done():
            await asyncio.wait({task}, timeout=.05)
            if not current() or asyncio.get_running_loop().time() >= deadline:
                raise PermissionError('Selected context request cancelled or expired.')
        source = await task
        if not current():
            raise PermissionError('Selected context permission changed.')
        if (not isinstance(source, ContextSource) or source.source_type != 'trusted_extension' or
                source.connection_id != metadata.id or source.resource_uri != selection or source.memory_id is not None or
                source.memory_revision is not None or source.screenshot_reference is not None or
                not isinstance(source.title, str) or len(source.title) > 256 or not isinstance(source.content, str) or
                len(source.content.encode('utf-8')) > 65536 or source.mime_type not in ('text/plain', 'text/markdown', 'application/json')):
            raise ValueError('Provider returned invalid, oversized or incorrectly attributed text.')
        return replace(source, retrieved_at=datetime.now(timezone.utc).isoformat())
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
