"""Bounded retrieval through the existing Phase 9 MemoryStore."""
import re

from src.memory.recall import _safe_fields


_STOP = {'this', 'that', 'with', 'from', 'about', 'what', 'when', 'where',
         'which', 'compare', 'saved', 'memory', 'memories', 'previous',
         'current', 'does', 'have', 'the', 'and', 'for'}
_FOLLOWUP = re.compile(r'\b(which one|those|these|that one|the saved one|it|them|they|earlier|previously)\b', re.I)


def _field_text(value):
    if isinstance(value, dict):
        return ' '.join(_field_text(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return ' '.join(_field_text(child) for child in value)
    return value if isinstance(value, str) else ''


class ContextRetrievalService:
    def __init__(self, store, permissions, *, limit=8, cancelled=None):
        self.store = store
        self.permissions = permissions
        self.cancelled = cancelled if callable(cancelled) else None
        self.limit = min(max(int(limit), 1), 8)

    def retrieve(self, session, request, *, filters=None):
        self.permissions.validate_request(session, request)
        if request.scope == 'current_only':
            return ()
        if request.scope == 'selected_memories':
            return tuple(record for memory_id in request.selected_memory_ids[:self.limit]
                         if (record := self.store.get_record(memory_id)) is not None)
        terms = [term for term in re.findall(r'\w+', request.user_question.casefold())
                 if len(term) >= 3 and term not in _STOP]
        field_terms = [term for term in re.findall(r'\w+',
            _field_text(_safe_fields(request.current_skill_result)).casefold())
            if len(term) >= 3 and term not in _STOP and term not in terms]
        found = {}
        if getattr(self.store, 'semantic_index', None):
            options = {'cancelled': self.cancelled} if self.cancelled else {}
            for hit in self.store.hybrid_search(request.user_question, filters=filters, limit=self.limit, **options):
                if hit.memory_id not in session.excluded_memory_ids:
                    found[hit.memory_id] = 2
        if request.conversation_enabled and _FOLLOWUP.search(request.user_question):
            for turn in session.history[-2:]:
                for _, memory_id, revision in turn.source_bindings:
                    if memory_id in session.excluded_memory_ids:
                        continue
                    record = self.store.get_record(memory_id)
                    if record is not None and record.updated_at == revision:
                        found[memory_id] = max(found.get(memory_id, 0), 3)
        for term in dict.fromkeys(terms[:8] + field_terms[:8]):
            weight = 2 if term in terms else 1
            for hit in self.store.search(term, filters=filters, limit=self.limit):
                if hit.memory_id in session.excluded_memory_ids:
                    continue
                found[hit.memory_id] = found.get(hit.memory_id, 0) + weight
        ids = sorted(found, key=lambda memory_id: (-found[memory_id], memory_id))[:self.limit]
        return tuple(record for memory_id in ids
                     if (record := self.store.get_record(memory_id)) is not None)
