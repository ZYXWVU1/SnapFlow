import json
import unittest

from src import prompts
from src.config import Config


class ModeTests(unittest.TestCase):
    def test_extract_accepts_observed_nested_code_response(self):
        from src.modes import parse_result
        from src.actions import copy_formats
        code = 'def total(values):\n    return sum(values)'
        response = json.dumps({'content_type': 'code', 'code': {'language': 'python', 'code': code}})
        result = parse_result('extract', response)
        self.assertEqual(result.data, {'content_type': 'code', 'language': 'python', 'code': code})
        self.assertEqual(copy_formats(result)['Copy Code'], code)

    def test_nested_extract_still_validates_fields(self):
        from src.modes import parse_result, ResponseFormatError
        for data in [
            {'content_type': 'code', 'code': {'language': 'python', 'code': 42}},
            {'content_type': 'code', 'code': {'code': 'print(1)'}},
            {'content_type': 'code', 'language': 'java', 'code': {'language': 'python', 'code': 'print(1)'}},
        ]:
            with self.subTest(data=data), self.assertRaises(ResponseFormatError):
                parse_result('extract', json.dumps(data))

    def test_phase_one_modes_and_migration(self):
        self.assertEqual(set(prompts.MODES), {'ask', 'debug', 'extract', 'explain', 'translate', 'smart'})
        for old, new in [('general', 'ask'), ('summarize', 'ask'), ('ocr', 'extract')]:
            self.assertEqual(Config(default_mode=old).default_mode, new)

    def test_structured_results_and_copy_formats(self):
        from src.modes import parse_result
        from src.actions import copy_formats
        result = parse_result('extract', '```json\n' + json.dumps({
            'content_type': 'table', 'headers': ['Name', 'Score'],
            'rows': [['A, B', 92], ['X|Y', None]]}) + '\n```')
        formats = copy_formats(result)
        self.assertIn('"A, B",92', formats['Copy CSV'])
        self.assertIn('X\\|Y', formats['Copy Markdown'])
        self.assertEqual(json.loads(formats['Copy JSON'])['rows'][0][1], 92)

    def test_invalid_structured_output_is_rejected(self):
        from src.modes import parse_result, ResponseFormatError
        for mode, data in [('debug', '{}'), ('debug', 'not json'),
                           ('extract', '{"content_type":"table","headers":["A"],"rows":[[1,2]]}'),
                           ('extract', '{"content_type":"code","code":42,"language":"python"}'),
                           ('extract', '{"content_type":"unsupported"}')]:
            with self.subTest(data=data), self.assertRaises(ResponseFormatError):
                parse_result(mode, data)

    def test_json_numbers_must_be_finite_at_any_depth(self):
        from src.modes import parse_result, ResponseFormatError
        for value in ('1e999', '{"nested":[1e999]}', 'NaN'):
            with self.subTest(value=value), self.assertRaises(ResponseFormatError):
                parse_result('extract', '{"content_type":"json","value":' + value + '}')

    def test_debug_fix_actions(self):
        from src.modes import parse_result
        from src.actions import copy_formats
        data = dict(error_type='IndexError', language='Python', summary='Out of range',
                    evidence=['a[len(a)]'], root_cause='Last index is len(a)-1',
                    fixes=[dict(before='a[len(a)]', after='a[-1]', explanation='Use final item')],
                    confidence='high')
        result = parse_result('debug', json.dumps(data))
        self.assertEqual(copy_formats(result)['Copy Fix'], 'a[-1]')
        self.assertIn('IndexError', copy_formats(result)['Copy Error'])

    def test_ask_is_plain_text(self):
        from src.modes import parse_result
        result = parse_result('ask', 'An ordinary answer')
        self.assertEqual(result.text, 'An ordinary answer')
        self.assertIsNone(result.data)
