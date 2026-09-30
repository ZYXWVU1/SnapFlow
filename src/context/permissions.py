"""Execution-time checks for session-scoped historical Memory access."""
from src.context.models import SCOPES


class ContextPermissionService:
    def grant_selected(self, session, memory_ids):
        ids = tuple(dict.fromkeys(memory_ids))
        if not ids or len(ids) > 8 or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError('Select one to eight saved memories.')
        session.scope = 'selected_memories'
        session.selected_memory_ids = ids
        session.excluded_memory_ids = ()
        session.permission_version += 1
        session.history.clear()

    def grant_search(self, session):
        session.scope = 'authorized_memory_search'
        session.selected_memory_ids = ()
        session.excluded_memory_ids = ()
        session.permission_version += 1
        session.history.clear()

    def revoke_permission(self, session):
        session.scope = 'current_only'
        session.selected_memory_ids = ()
        session.excluded_memory_ids = ()
        session.permission_version += 1
        # Old answers may contain private source text; never resend them after revocation.
        session.history.clear()

    def exclude_source(self, session, memory_id):
        if session.scope == 'selected_memories':
            remaining = tuple(item for item in session.selected_memory_ids if item != memory_id)
            if remaining == session.selected_memory_ids:
                return
            if not remaining:
                self.revoke_permission(session)
                return
            session.selected_memory_ids = remaining
        elif session.scope == 'authorized_memory_search':
            if memory_id in session.excluded_memory_ids:
                return
            session.excluded_memory_ids += (memory_id,)
        else:
            return
        session.permission_version += 1
        session.history.clear()

    def allowed_memory_ids(self, session):
        return session.selected_memory_ids if session.scope == 'selected_memories' else ()

    def can_search_memory(self, session):
        return session.scope == 'authorized_memory_search'

    def can_send_context_to_provider(self, session, request):
        self.validate_request(session, request)
        return True

    def validate_request(self, session, request):
        if (request.session_id != session.session_id or
                request.screenshot_reference != session.screenshot_reference or
                request.permission_version != session.permission_version):
            raise ValueError('Context session changed. Start a new request.')
        if request.scope not in SCOPES or request.scope != session.scope:
            raise ValueError('The requested context scope is not authorized.')
        if not isinstance(request.user_question, str) or not request.user_question.strip() or len(request.user_question) > 500:
            raise ValueError('Enter a question of at most 500 characters.')
        if not isinstance(request.current_skill_result, dict) or not request.current_skill_result:
            raise ValueError('Analyze the current screenshot before asking with context.')
        if request.scope == 'current_only':
            if request.selected_memory_ids or request.retrieval_enabled:
                raise ValueError('Current-only requests cannot include saved memories.')
        elif request.scope == 'selected_memories':
            if (not request.selected_memory_ids or request.retrieval_enabled or
                    not set(request.selected_memory_ids) <= set(session.selected_memory_ids)):
                raise ValueError('Only selected saved memories are authorized.')
        elif request.selected_memory_ids or not request.retrieval_enabled:
            raise ValueError('Memory search permission is required for retrieval.')
