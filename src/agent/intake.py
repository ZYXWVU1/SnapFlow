"""Recognized explicit prohibitions only narrow typed goal permissions."""
from dataclasses import replace
import re


def constrain_goal(goal, constraints):
    text = re.sub(r'\s+', ' ', goal.casefold().replace('’', "'"))
    updates = {}
    if re.search(r'(?:local (?:ai|processing) only|(?:only|just) (?:use )?local (?:ai|processing)|no (?:cloud|remote) (?:ai|processing))', text):
        updates['allow_remote_ai'] = False
    if re.search(r"(?:do not|don't|never) (?:send|submit|write externally)|no external writes", text):
        updates['allow_external_writes'] = False
    if re.search(r"(?:only|just) analy[sz]e|(?:do not|don't|never) save|no saving", text):
        updates['allow_local_writes'] = False
        updates['allow_external_writes'] = False
    if re.search(r"(?:do not|don't|never) (?:create|add) (?:calendar events|calendar reminders)|no calendar events", text):
        updates['forbidden_capabilities'] = tuple(dict.fromkeys((*constraints.forbidden_capabilities,
            'google_calendar_create_event', 'create_event', 'create_ics')))
    if re.search(r"(?:do not|don't|never) (?:search|use) (?:visual )?memor|no memory search", text):
        updates['allow_memory_search'] = False
    return replace(constraints, **updates)
