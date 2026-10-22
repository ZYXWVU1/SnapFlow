"""Execution-time checks for session-scoped historical Memory access."""
from src.context.models import SCOPES


class ContextPermissionService:
    def __init__(self, desktop_validator=None, browser_validator=None):
        self.desktop_validator = desktop_validator
        self.browser_validator = browser_validator

    def select_browser_sources(self, session, sources):
        from src.context.models import ContextSource
        sources = tuple(sources)
        if (len(sources) > 1 or any(not isinstance(source, ContextSource) or source.source_type != 'browser_dom'
                or not source.connection_id or not source.resource_uri or source.memory_id is not None
                or source.screenshot_reference is not None or len(source.content) > 3000 for source in sources)):
            raise ValueError('Select one bounded authorized browser source.')
        if sources and self.browser_validator is None:
            raise ValueError('Browser Context requires live authorization validation.')
        if self.browser_validator:
            self.browser_validator(sources)
        session.browser_sources = sources
        session.permission_version += 1
        session.history.clear()

    def select_desktop_sources(self, session, sources):
        from src.context.models import ContextSource
        from src.desktop_context.snapshot import DESKTOP_TYPES
        sources = tuple(sources)
        if len(sources) > 4 or any(not isinstance(s, ContextSource) or s.source_type not in DESKTOP_TYPES or
                not s.resource_uri or s.memory_id or s.connection_id or len(s.content) > 3000 for s in sources):
            raise ValueError('Select at most four bounded Desktop Context sources.')
        if len({s.source_type for s in sources}) != len(sources):
            raise ValueError('Duplicate Desktop Context sources.')
        if sources and self.desktop_validator is None:
            raise ValueError('Desktop Context requires live permission and freshness validation.')
        if self.desktop_validator:
            self.desktop_validator(sources)
        session.desktop_sources = sources
        session.permission_version += 1
        session.history.clear()

    def select_external_prompt(self, session, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > 16000:
            raise ValueError('External prompt exceeds its size limit.')
        session.external_prompt = text
        session.permission_version += 1
        session.history.clear()

    def select_external_sources(self, session, sources):
        from src.context.models import ContextSource
        sources = tuple(sources)
        if len(sources) > 4 or any(not isinstance(source, ContextSource) or
                source.source_type not in ('mcp_resource', 'trusted_extension') or not source.connection_id or not source.resource_uri or
                source.memory_id is not None or source.memory_revision is not None or source.screenshot_reference is not None or
                not isinstance(source.content, str) or len(source.content.encode('utf-8')) > 65536 for source in sources):
            raise ValueError('Select at most four bounded external text resources.')
        if len({(s.connection_id, s.resource_uri) for s in sources}) != len(sources):
            raise ValueError('Duplicate MCP resource selection.')
        session.external_sources = sources
        session.permission_version += 1
        session.history.clear()

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
        session.external_sources = ()
        session.external_prompt = ''
        session.desktop_sources = ()
        session.browser_sources = ()

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
        if request.browser_sources and self.browser_validator is None:
            raise ValueError('Browser Context requires a live validator.')
        if request.browser_sources != session.browser_sources:
            raise ValueError('Browser Context selection changed.')
        if self.browser_validator:
            self.browser_validator(request.browser_sources)
        if request.desktop_sources and self.desktop_validator is None:
            raise ValueError('Desktop Context requires a live validator.')
        if request.desktop_sources != session.desktop_sources:
            raise ValueError('Desktop Context selection changed.')
        if self.desktop_validator:
            self.desktop_validator(request.desktop_sources)
        if request.external_sources != session.external_sources or request.external_prompt != session.external_prompt:
            raise ValueError('Only explicitly selected MCP resources are authorized.')
        if (request.session_id != session.session_id or
                request.screenshot_reference != session.screenshot_reference or
                request.permission_version != session.permission_version):
            raise ValueError('Context session changed. Start a new request.')
        if request.scope not in SCOPES or request.scope != session.scope:
            raise ValueError('The requested context scope is not authorized.')
        if not isinstance(request.user_question, str) or not request.user_question.strip() or len(request.user_question) > 500:
            raise ValueError('Enter a question of at most 500 characters.')
        if (not isinstance(request.current_skill_result, dict) or not request.current_skill_result) and not request.browser_sources:
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
