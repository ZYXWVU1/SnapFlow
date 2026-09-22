"""Small display strings for Smart pipeline progress."""
LABELS = {'code_error': 'Code Error', 'table': 'Table', 'assignment': 'Assignment',
          'event': 'Event / Meeting', 'unknown': 'Unknown'}


def detection_notice(result, route):
    text = f'Detected: {LABELS.get(result.content_type, "Unknown")} | Confidence: {result.confidence:.0%}'
    if route == 'ask':
        text += "\nI couldn't confidently determine a specialized workflow. Opening Ask mode instead."
    return text


def loading_message(route):
    return {'debug': 'Analyzing error...', 'extract': 'Extracting data...',
            'code_error': 'Diagnosing error...', 'table': 'Extracting data...',
            'assignment': 'Extracting assignment details...', 'event': 'Extracting event details...',
            'ask': 'Answering in Ask mode...'}.get(route, 'Preparing result...')
