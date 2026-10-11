"""Conservative deterministic triggers. No AI, Memory search or tool calls."""
import re

_RULES = {
    'assignment': ('Want to organize this assignment?', 'Review requirements and build a plan.', 'Organize this assignment.'),
    'code_error': ('Want help understanding this error?', 'Review the error and choose related memories.', 'Help me debug what I am currently working on.'),
    'event': ('Want to preview these event details?', 'Review dates before choosing a calendar action.', 'Extract and review these event details.'),
    'job_posting': ('Want to review this opportunity?', 'Compare requirements with memories you select.', 'Compare this opportunity with my selected memories.')}


def candidates(snapshot, *, screenshot_skill=None, selection_allowed=False, related_memory_count=0):
    kind, trigger = None, 'explicit_screenshot'
    if screenshot_skill in _RULES:
        kind = screenshot_skill
    elif selection_allowed and snapshot.selection:
        text = snapshot.selection.text.casefold()
        trigger = 'selected_text'
        if re.search(r'\bassignment\b', text) and re.search(r'\b(?:due|submit|requirements)\b', text):
            kind = 'assignment'
        elif re.search(r'\b(?:traceback|exception|compiler error)\b', text):
            kind = 'code_error'
        elif re.search(r'\b(?:job|internship)\b', text) and re.search(r'\b(?:requirements|qualifications)\b', text):
            kind = 'job_posting'
    if kind:
        title, description, goal = _RULES[kind]
        return ((kind, title, description, goal, trigger),)
    if related_memory_count > 0:
        return (('related_memory', 'Related memories are available.',
            'Choose which saved memories to include.', 'Review my selected related memories.', 'selected_memory'),)
    return ()
