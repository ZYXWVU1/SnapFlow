import csv
import io
import json
import unittest

from src.skills.registry import SKILLS
from src.modes import ResponseFormatError
from src.skill_actions import ACTIONS, execute_action


class SkillTests(unittest.TestCase):
    def parse(self, kind, data):
        return SKILLS[kind].parse(json.dumps(data), .94)

    def test_assignment_normalizes_partial_fields(self):
        result = self.parse('assignment', dict(title=' Homework 4 ', due_date='2026-09-28',
            due_time='11:59 PM', points=100, assignment_type='strange', extra='ignored'))
        self.assertEqual(result.data['due_time'], '23:59')
        self.assertEqual(result.data['title'], 'Homework 4')
        self.assertEqual(result.data['assignment_type'], 'other')
        self.assertIsNone(result.data['course'])
        self.assertNotIn('extra', result.data)
        self.assertEqual(result.confidence, .94)

    def test_invalid_fields_become_null_with_warnings(self):
        result = self.parse('assignment', dict(due_date='2026-02-30', due_time='25:00', points=True))
        for key in ('due_date', 'due_time', 'points'):
            self.assertIsNone(result.data[key])
        self.assertTrue(result.warnings)
        self.assertFalse(ACTIONS['create_ics'].enabled(result))

    def test_event_fields_and_unsafe_url(self):
        result = self.parse('event', dict(title='Club', date='2026-09-24', start_time='3:00 PM',
            meeting_platform='Zoom', meeting_url='https://zoom.us/j/123', passcode='00123'))
        self.assertEqual(result.data['start_time'], '15:00')
        self.assertEqual(result.data['meeting_platform'], 'zoom')
        self.assertEqual(result.data['passcode'], '00123')
        self.assertIn('copy_meeting_link', result.actions)
        invalid = self.parse('event', dict(meeting_url='javascript:alert(1)'))
        self.assertIsNone(invalid.data['meeting_url'])
        self.assertNotIn('copy_meeting_link', invalid.actions)

    def test_code_error_partial_fixes(self):
        result = self.parse('code_error', dict(error_type='IndexError', line=0,
            evidence=['a[9]'], suggested_fixes=[dict(title='Check length', explanation='Check bounds')]))
        self.assertIsNone(result.data['line'])
        self.assertIsNone(result.data['suggested_fixes'][0]['before'])
        self.assertIn('Check bounds', execute_action('copy_fix', result).payload)

    def test_code_fix_preserves_indentation(self):
        code = '    if ready:\n        run()\n    done()\n'
        result = self.parse('code_error', {'suggested_fixes': [{'before': code, 'after': code}]})
        self.assertEqual(result.data['suggested_fixes'][0]['before'], code)
        self.assertEqual(result.data['suggested_fixes'][0]['after'], code)
        self.assertIn(code, execute_action('copy_fix', result).payload)

    def test_table_preserves_values_pads_and_exports(self):
        result = self.parse('table', dict(headers=['Name', 'Value'], rows=[['00123', 'a,b'], ['a|b'], ['x', '"quoted"\nline']]))
        self.assertEqual(result.data['rows'][1], ['a|b', ''])
        payload = execute_action('copy_csv', result).payload
        self.assertEqual(list(csv.reader(io.StringIO(payload)))[1], ['00123', 'a,b'])
        self.assertIn('a\\|b', execute_action('copy_markdown', result).payload)
        self.assertEqual(json.loads(execute_action('copy_json', result).payload), result.data)

    def test_missing_headers_and_long_rows_preserve_cells(self):
        result = self.parse('table', dict(rows=[['a', 'b'], ['c', 'd', 'e']]))
        self.assertEqual(len(result.data['headers']), 3)
        self.assertEqual(result.data['rows'][1][-1], 'e')
        self.assertTrue(result.warnings)

    def test_json_fences_and_invalid_shapes(self):
        for skill in SKILLS.values():
            self.assertEqual(skill.parse('```json\n{}\n```', .8).skill_id, skill.id)
            for raw in ('[]', 'not json', '{"x":NaN}', '{"x":1e999}'):
                with self.subTest(skill=skill.id, raw=raw), self.assertRaises(ResponseFormatError):
                    skill.parse(raw, .8)
            self.assertIn('Never invent missing data.', skill.prompt())

    def test_calendar_all_day_and_timed(self):
        result = self.parse('assignment', dict(course='CS', title='HW', due_date='2026-09-28'))
        ics = execute_action('create_ics', result).payload
        self.assertIn('DTSTART;VALUE=DATE:20260928\r\n', ics)
        self.assertIn('DTEND;VALUE=DATE:20260929\r\n', ics)
        self.assertIn('BEGIN:VEVENT\r\n', ics)
        self.assertIn('UID:', ics)
        self.assertIn('DTSTAMP:', ics)
        timed = self.parse('event', dict(title='A, B; C\nNext', date='2026-09-24', start_time='15:00'))
        ics = execute_action('create_ics', timed).payload
        self.assertIn('DTSTART:20260924T150000', ics)
        self.assertIn('SUMMARY:A\\, B\\; C\\nNext', ics)
        self.assertNotIn('DTEND:', ics)

    def test_calendar_folds_unicode_and_disallows_missing_dates(self):
        result = self.parse('event', dict(title='会议' * 100, date='2026-09-24'))
        ics = execute_action('create_ics', result).payload
        self.assertTrue(all(len(line.encode('utf-8')) <= 75 for line in ics.split('\r\n')))
        self.assertFalse(execute_action('create_ics', self.parse('event', {})).success)

    def test_calendar_timezone_offsets_and_ambiguous_zone(self):
        data = dict(title='Call', date='2026-09-24', start_time='15:00', end_time='16:00', timezone='UTC-04:00')
        result = self.parse('event', data)
        ics = execute_action('create_ics', result).payload
        self.assertIn('DTSTART:20260924T190000Z', ics)
        self.assertIn('DTEND:20260924T200000Z', ics)
        result = self.parse('event', {**data, 'timezone': 'CST'})
        self.assertFalse(execute_action('create_ics', result).success)

    def test_calendar_iana_timezone_and_dst_ambiguity(self):
        data = dict(date='2026-09-24', start_time='15:00', timezone='America/New_York')
        self.assertIn('DTSTART:20260924T190000Z', execute_action('create_ics', self.parse('event', data)).payload)
        for day, clock in [('2026-11-01', '01:30'), ('2026-03-08', '02:30')]:
            result = self.parse('event', {**data, 'date': day, 'start_time': clock})
            self.assertFalse(execute_action('create_ics', result).success)

    def test_event_overnight_end_is_not_guessed(self):
        result = self.parse('event', dict(date='2026-09-24', start_time='23:00', end_time='01:00'))
        self.assertIsNone(result.data['end_time'])
        self.assertTrue(result.warnings)
        self.assertNotIn('DTEND:', execute_action('create_ics', result).payload)

    def test_table_rejects_nested_cells(self):
        for data in ({'rows': [[{}]]}, {'headers': [4]}, {'rows': [42]}):
            with self.assertRaises(ResponseFormatError):
                self.parse('table', data)

    def test_actions_cannot_execute_for_wrong_skill(self):
        self.assertFalse(execute_action('copy_csv', self.parse('assignment', {})).success)
        self.assertFalse(execute_action('unknown', self.parse('table', {})).success)

    def test_diagnostics_support_languages_and_multiple_fixes(self):
        for language, error in [('Python', 'IndexError'), ('Java', 'NullPointerException'), ('C++', 'compiler error')]:
            result = self.parse('code_error', dict(language=language, error_type=error,
                suggested_fixes=[{'after': 'fixed()'}, {'explanation': 'Check the input'}]))
            self.assertEqual(result.data['error_type'], error)
            self.assertEqual(len(result.data['suggested_fixes']), 2)
            self.assertIn('fixed()', execute_action('copy_fix', result).payload)
