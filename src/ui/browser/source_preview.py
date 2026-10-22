"""Readable rendering of the exact bounded browser AI source."""
import json


def source_has_content(content):
    try:
        data = json.loads(content)
        return bool(data.get('relevant_text', '').strip() or any(data.get('headings', ())) or data.get('form_fields'))
    except (ValueError, TypeError, AttributeError):
        return False


def render_source(content):
    try:
        data = json.loads(content)
        sections = [data.get('page_title', ''), data.get('origin', ''), data.get('relevant_text', '')]
        if data.get('headings'):
            sections.append('Headings\n' + '\n'.join('• ' + str(item) for item in data['headings']))
        if data.get('form_fields'):
            sections.append('Form fields (values excluded)\n' + '\n'.join(
                '• ' + str(item.get('label') or item.get('role') or 'Unlabeled control') for item in data['form_fields']))
        return '\n\n'.join(str(item) for item in sections if item)
    except (ValueError, TypeError, AttributeError):
        return 'Page source is unavailable. Refresh the authorized page context.'
