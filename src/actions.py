"""Local, side-effect-free copy representations for validated mode results."""
import csv
import io
import json

from src.modes import ModeResult


def display_value(value) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def copy_formats(result: ModeResult) -> dict[str, str]:
    data = result.data
    if data is None:
        return {'Copy': result.text}
    if result.mode == 'debug':
        actions = {'Copy Error': data['error_type'] + '\n' + '\n'.join(data['evidence'])}
        fixes = [fix['after'] for fix in data['fixes'] if fix['after'].strip()]
        if fixes:
            actions['Copy Fix'] = '\n\n'.join(fixes)
        return actions
    actions = {'Copy JSON': json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)}
    kind = data['content_type']
    if kind == 'table':
        output = io.StringIO(newline='')
        csv.writer(output).writerows([data['headers'], *data['rows']])
        actions['Copy CSV'] = output.getvalue()
        def row(values):
            return '| ' + ' | '.join(display_value(x).replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', '<br>').replace('\n', '<br>').replace('\r', '<br>') for x in values) + ' |'
        actions['Copy Markdown'] = '\n'.join([row(data['headers']), row(['---'] * len(data['headers'])), *[row(r) for r in data['rows']]])
    elif kind == 'code':
        actions['Copy Code'] = data['code']
    elif kind in ('text', 'url'):
        actions['Copy Text'] = data[kind]
    elif kind == 'json':
        actions['Copy JSON'] = json.dumps(data['value'], ensure_ascii=False, indent=2, allow_nan=False)
    else:
        actions['Copy Details'] = '\n'.join(f'{k.replace("_", " ").title()}: {display_value(v) or "Not visible"}' for k, v in data.items() if k != 'content_type')
    return actions
