"""Recommend existing safe Actions and previewable Workflows without executing them."""
from dataclasses import dataclass
from uuid import uuid4

from src.skill_actions import ACTIONS
from src.skills.base import SkillResult


@dataclass(frozen=True)
class ContextActionSuggestion:
    suggestion_id: str
    request_id: str
    session_id: str
    permission_version: int
    action_id: str
    label: str
    description: str
    source_ids: tuple[str, ...]
    memory_revisions: tuple[tuple[str, str], ...]
    result_identity: int
    workflow_id: str | None = None
    requires_confirmation: bool = True


class ActionSuggestionService:
    def __init__(self, store, permissions):
        self.store = store
        self.permissions = permissions

    def suggest(self, session, request, result, contextual_result, workflow_registry):
        self.permissions.validate_request(session, request)
        if (not isinstance(result, SkillResult) or not contextual_result.source_ids or
                contextual_result.request_id != request.request_id):
            return ()
        revisions = tuple((source.memory_id, source.memory_revision)
            for source in contextual_result.sources if source.memory_id is not None)
        source_ids = contextual_result.source_ids

        def make(action_id, label, description, *, workflow_id=None, confirmation=True):
            return ContextActionSuggestion(uuid4().hex, request.request_id,
                session.session_id, session.permission_version, action_id,
                label, description, source_ids, revisions, id(result),
                workflow_id, confirmation)

        suggestions = []
        for action_id in dict.fromkeys(result.actions):
            action = ACTIONS.get(action_id)
            if action is not None and action.kind == 'copy' and action.enabled(result):
                suggestions.append(make(action_id, action.label,
                    'Copy details from the current screenshot result.', confirmation=False))
            if len(suggestions) >= 2:
                break
        for workflow in workflow_registry.matching(result.skill_id):
            if workflow.enabled:
                suggestions.append(make('workflow_preview', 'Preview ' + workflow.name,
                    'Inspect the existing Workflow without running its actions.',
                    workflow_id=workflow.id))
            if len(suggestions) >= 4:
                break
        return tuple(suggestions)

    def validate(self, suggestion, session, result, workflow_registry):
        if (suggestion.session_id != session.session_id or
                suggestion.permission_version != session.permission_version or
                suggestion.result_identity != id(result) or
                not isinstance(result, SkillResult)):
            raise ValueError('This suggestion is no longer available.')
        for memory_id, revision in suggestion.memory_revisions:
            record = self.store.get_record(memory_id)
            if record is None or record.updated_at != revision:
                raise ValueError('A source for this suggestion is no longer available.')
        if suggestion.action_id == 'workflow_preview':
            if not suggestion.workflow_id or not any(workflow.id == suggestion.workflow_id
                and workflow.enabled for workflow in workflow_registry.matching(result.skill_id)):
                raise ValueError('This Workflow is no longer available.')
        else:
            action = ACTIONS.get(suggestion.action_id)
            if (action is None or action.kind != 'copy' or
                    suggestion.action_id not in result.actions or not action.enabled(result)):
                raise ValueError('This action is no longer available.')
        return True
