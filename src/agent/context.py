"""Assemble explicit Agent sources using the Phase 10 permission engine."""
from dataclasses import replace
import re

from src.context.assembler import ContextAssembler
from src.context.models import ContextRequest, ContextSession
from src.context.permissions import ContextPermissionService
from src.context.retrieval import ContextRetrievalService


def assemble_agent_context(memory_store, current_result, screenshot_reference, goal,
        selected_memory_ids=(), allow_search=False, conversation_history=(), external_sources=(), *, include_current=True,
        desktop_sources=(), desktop_validator=None):
    if type(allow_search) is not bool:
        raise ValueError('Memory search must be explicitly authorized.')
    if type(include_current) is not bool:
        raise ValueError('Current screenshot context must be explicitly selected.')
    if not include_current:
        current_result = {'context_status': 'No current screenshot selected.'}
        screenshot_reference = 'agent-no-capture'
    session = ContextSession.new(screenshot_reference)
    permissions = ContextPermissionService(desktop_validator)
    selected_memory_ids = tuple(selected_memory_ids)
    if selected_memory_ids and allow_search:
        raise ValueError('Choose selected memories or authorized search.')
    if selected_memory_ids:
        permissions.grant_selected(session, selected_memory_ids)
    elif allow_search:
        permissions.grant_search(session)
    if external_sources:
        permissions.select_external_sources(session, external_sources)
    if desktop_sources:
        if desktop_validator is None:
            raise ValueError('Desktop Context requires a live permission validator.')
        permissions.select_desktop_sources(session, desktop_sources)
    request = ContextRequest.new(session, goal, current_skill_result=current_result,
        conversation_enabled=bool(conversation_history))
    permissions.validate_request(session, request)
    records = ContextRetrievalService(memory_store, permissions).retrieve(session, request)
    if selected_memory_ids and {record.id for record in records} != set(selected_memory_ids):
        raise ValueError('A selected memory is no longer available. Select sources again.')
    assembler = ContextAssembler()
    sources = assembler.build_context(request, records, ()).sources
    current_refs = {source.memory_id: (source.source_id, source.memory_revision)
        for source in sources if source.memory_id is not None}
    safe_history = []
    for turn in tuple(conversation_history)[-4:]:
        if any(current_refs.get(memory_id, (None, None))[1] != revision
                for memory_id, revision in turn.memory_revisions):
            continue
        bindings = {old_id: current_refs[memory_id][0]
            for old_id, memory_id, revision in turn.source_bindings
            if current_refs.get(memory_id, (None, None))[1] == revision}
        if len(bindings) != len(turn.source_bindings):
            continue
        safe_history.append(replace(turn, answer=re.sub(r'\[(M\d+)\]',
            lambda match: f'[{bindings.get(match.group(1), match.group(1))}]', turn.answer)))
    permissions.can_send_context_to_provider(session, request)
    bundle = assembler.build_context(request, records, safe_history)
    if not include_current:
        prompt = '\n'.join(line for line in bundle.prompt.splitlines()
            if not line.startswith('[C1] current_skill_result:'))
        bundle = replace(bundle, prompt=prompt, sources=tuple(source for source in bundle.sources if source.source_id != 'C1'))
    return bundle
