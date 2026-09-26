import unittest
from unittest.mock import Mock

from src.skill_actions import ACTIONS
from src.skills.base import SkillResult
from src.integrations.actions import prepare, preview, validate_config


class CloudActionTests(unittest.TestCase):
    def setUp(self):
        self.result = SkillResult('assignment', 'Assignment', 0.9,
            {'course': 'CS 220', 'title': 'Homework', 'due_date': '2026-09-30',
             'due_time': '23:59'}, [])

    def test_registered_actions_and_field_validation(self):
        self.assertEqual(ACTIONS['google_calendar_create_event'].risk_level, 'external_write')
        config = {'calendar_id': 'primary', 'title': '{course}: {title}',
                  'date_field': 'due_date', 'start_time_field': 'due_time',
                  'timezone': 'America/New_York'}
        self.assertEqual(validate_config('google_calendar_create_event', config,
            {'course', 'title', 'due_date', 'due_time'}), [])
        self.assertIn('Unknown Skill field', ' '.join(validate_config(
            'google_calendar_create_event', {**config, 'date_field': 'nonexistent'},
            {'course', 'title', 'due_date', 'due_time'})))

    def test_calendar_prepare_and_preview_are_pure(self):
        config = {'calendar_id': 'primary', 'title': '{course}: {title}',
                  'date_field': 'due_date', 'start_time_field': 'due_time',
                  'timezone': 'America/New_York'}
        self.assertEqual(prepare('google_calendar_create_event', self.result, config)['event_date'], '2026-09-30')
        self.assertIn('CS 220: Homework', preview('google_calendar_create_event', self.result, config))
        with self.assertRaises(ValueError):
            prepare('google_calendar_create_event', SkillResult('assignment', 'Assignment', .9,
                {**self.result.data, 'due_date': None}, []), config)

    def test_sheets_dynamic_mapping_and_todoist_optional_due(self):
        sheet = {'spreadsheet_id': 'spreadsheet123456', 'tab': 'Applications',
                 'columns': [{'column': 'Course', 'field': 'course'},
                             {'column': 'Assignment', 'field': 'title'}],
                 'create_header': True}
        self.assertEqual(validate_config('google_sheets_append_row', sheet,
            {'course', 'title', 'due_date'}), [])
        self.assertEqual(prepare('google_sheets_append_row', self.result, sheet)['columns'],
                         [('Course', 'CS 220'), ('Assignment', 'Homework')])
        self.assertIn('Course: CS 220', preview('google_sheets_append_row', self.result, sheet))
        self.assertIn('Unknown Skill field', ' '.join(validate_config(
            'google_sheets_append_row', {**sheet, 'columns': [{'column': 'Bad', 'field': 'secret'}]},
            {'course', 'title'})))
        task = {'project_id': '123', 'title': 'Do {title}', 'due_date_field': 'due_date', 'priority': 2}
        self.assertEqual(prepare('todoist_create_task', self.result, task)['due_date'], '2026-09-30')
        task_result = SkillResult('assignment', 'Assignment', .9, {**self.result.data, 'due_date': None}, [])
        self.assertIsNone(prepare('todoist_create_task', task_result, task)['due_date'])

    def test_dry_run_rejects_invalid_timing_before_any_write(self):
        base = {'calendar_id': 'primary', 'title': '{title}', 'date_field': 'due_date',
                'start_time_field': 'due_time'}
        with self.assertRaises(ValueError):
            preview('google_calendar_create_event', self.result, base)
        without_date = SkillResult('assignment', 'Assignment', .9,
            {**self.result.data, 'due_date': None}, [])
        with self.assertRaises(ValueError):
            preview('todoist_create_task', without_date,
                {'title': '{title}', 'due_date_field': 'due_date', 'due_time_field': 'due_time',
                 'timezone': 'America/New_York'})


if __name__ == '__main__':
    unittest.main()
