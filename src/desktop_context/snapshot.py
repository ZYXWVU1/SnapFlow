"""Deterministic relevance, deduplication and the existing ContextSource bridge."""
from datetime import datetime, timezone
import json
import re

from src.context.models import ContextSource

DESKTOP_TYPES = frozenset(('desktop_window', 'desktop_ui', 'selected_text', 'clipboard'))


class DesktopContextRelevanceFilter:
    def sources(self, snapshot, goal='', excluded=()):
        sources = []
        seen = set()
        def add(kind, title, value, confidence):
            if kind in excluded or not value:
                return
            content = json.dumps({'text': value, 'confidence': confidence,
                'captured_at': snapshot.captured_at, 'untrusted': True}, ensure_ascii=False)
            normalized = re.sub(r'\s+', ' ', str(value)).strip().casefold()
            if normalized in seen:
                return
            seen.add(normalized)
            sources.append(ContextSource(f'D{len(sources) + 1}', kind, title, content[:3000],
                resource_uri=snapshot.snapshot_id, retrieved_at=snapshot.captured_at))
        if snapshot.selection:
            add('selected_text', 'Selected text · desktop', snapshot.selection.text, 'high')
        elements = snapshot.ui_elements
        terms = set(re.findall(r'\w{3,}', goal.casefold()))
        ordered = sorted(elements, key=lambda e: (not e.focused,
            -len(terms & set(re.findall(r'\w{3,}', (e.name or '').casefold()))), e.depth))
        chunks = []
        used = {re.sub(r'\s+', ' ', snapshot.selection.text).strip().casefold()} if snapshot.selection else set()
        for element in ordered[:40]:
            text = ' '.join(x for x in (element.name, element.value) if x)
            if not text or text.casefold() in used:
                continue
            used.add(text.casefold())
            chunks.append({'control': element.control_type, 'text': text, 'focused': bool(element.focused)})
        add('desktop_ui', 'Focused UI and relevant structure', chunks, 'medium')
        if snapshot.active_window:
            window = snapshot.active_window
            add('desktop_window', 'Current application/window', {'app': window.app_name,
                'process': window.process_name, 'title': window.window_title, 'category': window.category}, 'high')
        add('clipboard', 'Current clipboard · explicitly permitted', snapshot.clipboard_text, 'medium')
        return tuple(sources)


def is_stale(snapshot, *, max_age_seconds=120):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot.captured_at)).total_seconds()
        return not 0 <= age <= max_age_seconds
    except (ValueError, TypeError):
        return True
