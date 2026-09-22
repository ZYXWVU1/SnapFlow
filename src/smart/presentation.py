"""Small display strings for Smart pipeline progress and placeholders."""
LABELS = {'code_error': 'Code Error', 'table': 'Table', 'assignment': 'Assignment',
          'event': 'Event / Meeting', 'unknown': 'Unknown'}


def detection_notice(result, route):
    text = f'Detected: {LABELS.get(result.content_type, "Unknown")} | Confidence: {result.confidence:.0%}'
    if route == 'ask':
        text += "\nI couldn't confidently determine a specialized workflow. Opening Ask mode instead."
    return text


def loading_message(route):
    return {'debug': 'Analyzing error...', 'extract': 'Extracting data...',
            'ask': 'Answering in Ask mode...'}.get(route, 'Preparing result...')


def placeholder_message(route):
    return f'{LABELS[route]} extraction will be available in the next phase.\n\nYou can ask AI about this screenshot now.'
