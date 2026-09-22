import math

from .base import Skill
from .validation import string_list, text
from src.prompts import TABLE_GUIDANCE


class TableSkill(Skill):
    id, title, view = 'table', 'Table', 'table'
    schema = {'title': 'string or null', 'headers': ['string'], 'rows': [['string']], 'notes': ['string']}
    instructions = TABLE_GUIDANCE + ' Preserve leading zeros, currency symbols and percentages as strings.'
    action_ids = ('copy_csv', 'copy_json', 'copy_markdown', 'copy_table_text', 'ask_ai')
    presentation = (('title', 'Title'), ('notes', 'Notes'))

    def normalize(self, raw):
        headers, rows = raw.get('headers', []), raw.get('rows', [])
        if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
            raise ValueError('Invalid headers')
        if not isinstance(rows, list):
            raise ValueError('Invalid rows')
        converted = []
        for row in rows:
            if not isinstance(row, list):
                raise ValueError('Invalid row')
            cells = []
            for cell in row:
                if cell is None:
                    cells.append('')
                elif isinstance(cell, str):
                    cells.append(cell)
                elif type(cell) in (int, float, bool) and (not isinstance(cell, float) or math.isfinite(cell)):
                    cells.append(str(cell).lower() if isinstance(cell, bool) else str(cell))
                else:
                    raise ValueError('Invalid cell')
            converted.append(cells)
        width = max([len(headers), *(len(r) for r in converted)])
        warnings = []
        if len(headers) != width or any(len(r) != width for r in converted):
            warnings.append('Some cells may have been interpreted incorrectly; missing cells padded and missing headers labeled.')
        headers = headers + [f'Column {i + 1}' for i in range(len(headers), width)]
        return {'title': text(raw.get('title')), 'headers': headers,
                'rows': [r + [''] * (width - len(r)) for r in converted],
                'notes': string_list(raw.get('notes'))}, warnings
