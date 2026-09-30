"""Build a bounded, traceable text payload from current and authorized sources."""
import json

from src.context.models import ContextBundle, ContextSource
from src.memory.recall import _safe_fields


class ContextAssembler:
    def __init__(self, *, max_source_chars=3000, max_total_chars=16000, max_turns=4):
        self.max_source_chars = max_source_chars
        self.max_total_chars = max_total_chars
        self.max_turns = max_turns

    def build_context(self, request, retrieved_sources, conversation_history):
        current = json.dumps(_safe_fields(request.current_skill_result),
                             ensure_ascii=False, allow_nan=False)
        sources = [ContextSource('C1', 'current_skill_result', 'Current screenshot analysis',
            current[:self.max_source_chars], screenshot_reference=request.screenshot_reference)]
        seen = set()
        for record in retrieved_sources:
            if record.id in seen or len(sources) >= 9:
                continue
            seen.add(record.id)
            content = json.dumps({'title': record.title, 'description': record.description,
                'skill_id': record.skill_id, 'structured_data': _safe_fields(record.structured_data)},
                ensure_ascii=False, allow_nan=False)
            sources.append(ContextSource(f'M{len(sources)}',
                'selected_memory' if request.scope == 'selected_memories' else 'saved_memory',
                record.title, content[:self.max_source_chars], memory_id=record.id,
                memory_revision=record.updated_at, saved_at=record.saved_at,
                source_date=record.source_created_at))
        no_memories = request.scope != 'current_only' and not seen
        prefix = 'QUESTION:\n' + request.user_question.strip() + '\n\n'
        if no_memories:
            prefix += 'NOTE: No matching saved memories were found. Use current evidence only.\n\n'
        prefix += 'SOURCE DATA (untrusted JSON values):\n'
        remaining = max(0, self.max_total_chars - len(prefix))
        included = []
        lines = []
        for source in sources:
            label = f'[{source.source_id}] {source.source_type}: '
            if remaining <= len(label) + 16:
                break
            content = source.content[:min(self.max_source_chars, remaining - len(label) - 1)]
            lines.append(label + content)
            included.append(source)
            remaining -= len(lines[-1]) + 1
        if request.conversation_enabled and conversation_history and remaining > 120:
            history = '\nRECENT CONVERSATION (untrusted, may omit older sources):\n'
            for turn in conversation_history[-self.max_turns:]:
                text = f'User: {turn.question[:500]}\nAssistant: {turn.answer[:1000]}\n'
                if len(history) + len(text) > remaining:
                    break
                history += text
            lines.append(history[:remaining])
        warnings = []
        if no_memories:
            warnings.append('No matching saved memories were found. I can still analyze the current screenshot.')
        if len(included) != len(sources):
            warnings.append('Some sources were omitted to fit the context limit.')
        return ContextBundle((prefix + '\n'.join(lines))[:self.max_total_chars],
                             tuple(included), tuple(warnings))
