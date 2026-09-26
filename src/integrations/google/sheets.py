"""Fixed Google Sheets row append with header validation."""
import re
from urllib.parse import quote


_SPREADSHEET_ID = re.compile(r'[A-Za-z0-9_-]{10,}\Z')


class GoogleSheetsProvider:
    def __init__(self, api):
        self.api = api

    def list_sheets(self, spreadsheet_id):
        self._validate_id(spreadsheet_id)
        response = self.api.request('GET', f'spreadsheets/{spreadsheet_id}',
                                    params={'fields': 'sheets(properties(sheetId,title))'})
        return [item.get('properties', {}) for item in response.get('sheets', [])]

    def test_connection(self, spreadsheet_id):
        self.list_sheets(spreadsheet_id)
        return True

    @staticmethod
    def _validate_id(spreadsheet_id):
        if not isinstance(spreadsheet_id, str) or not _SPREADSHEET_ID.fullmatch(spreadsheet_id):
            raise ValueError('Enter a valid spreadsheet ID.')

    def append_row(self, spreadsheet_id, tab, columns, *, create_header=False):
        self._validate_id(spreadsheet_id)
        if not isinstance(tab, str) or not tab.strip() or any(ord(c) < 32 for c in tab):
            raise ValueError('Choose a sheet tab.')
        if not isinstance(columns, (list, tuple)) or not 1 <= len(columns) <= 100 or any(
            not isinstance(pair, (list, tuple)) or len(pair) != 2 or
            not isinstance(pair[0], str) or not pair[0].strip() for pair in columns
        ):
            raise ValueError('Configure at least one valid column mapping.')
        headers = [pair[0] for pair in columns]
        if len(set(headers)) != len(headers):
            raise ValueError('Sheet column names must be unique.')
        values = ['' if pair[1] is None else str(pair[1]) for pair in columns]
        tab_range = "'" + tab.replace("'", "''") + "'!"
        prefix = f'spreadsheets/{spreadsheet_id}/values/'
        header_response = self.api.request('GET', prefix + quote(tab_range + '1:1', safe='!'))
        existing = header_response.get('values', [])
        if existing and existing[0] != headers:
            raise ValueError('Sheet columns do not match configured mapping.')
        rows = ([headers] if not existing and create_header else []) + [values]
        return self.api.request('POST', prefix + quote(tab_range + 'A:ZZ', safe='!') + ':append',
            json={'majorDimension': 'ROWS', 'values': rows},
            params={'valueInputOption': 'RAW', 'insertDataOption': 'INSERT_ROWS'}, write=True)
