import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel
from src.skills.registry import SKILLS
from src.ui.result_window import ResultWindow

APP = QApplication.instance() or QApplication([])


class SkillUITests(unittest.TestCase):
    def setUp(self):
        self.window = ResultWindow('smart', False)

    def tearDown(self):
        self.window.close()

    def render(self, kind, data):
        self.window.set_skill_result(SKILLS[kind].parse(json.dumps(data), .9))
        self.window.show()
        APP.processEvents()

    def test_preview_is_bounded_but_exports_all_rows(self):
        self.render('table', {'headers': ['Code'], 'rows': [[f'{i:05}'] for i in range(100)]})
        self.assertEqual(self.window.structured.table.rowCount(), 10)
        self.window.action_buttons['Copy CSV'].click()
        self.assertIn('00099', APP.clipboard().text())
        self.assertEqual(len(APP.clipboard().text().splitlines()), 101)

    def test_missing_fields_hidden_and_calendar_disabled(self):
        self.render('assignment', {'title': 'Homework'})
        self.assertNotIn('Due date', self.window.structured.section_titles)
        self.assertFalse(self.window.action_buttons['Create Calendar File'].isEnabled())
        self.assertTrue(self.window.action_buttons['Ask AI'].isEnabled())
        self.assertFalse(any(label.text() == 'None' for label in self.window.structured.findChildren(QLabel)))

    def test_calendar_save_cancel_and_failure(self):
        self.render('event', {'title': 'Club', 'date': '2026-09-24'})
        button = self.window.action_buttons['Create Calendar File']
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'event.ics'
            with patch('src.ui.skill_actions.QFileDialog.getSaveFileName', return_value=(str(target), '')):
                button.click()
            self.assertIn(b'BEGIN:VCALENDAR\r\n', target.read_bytes())
            self.assertEqual(self.window.action_status.text(), 'Calendar file created')
            with patch('src.ui.skill_actions.QFileDialog.getSaveFileName', return_value=('', '')), patch.object(Path, 'write_bytes') as write:
                button.click()
                write.assert_not_called()
            with patch('src.ui.skill_actions.QFileDialog.getSaveFileName', return_value=(str(target), '')), patch.object(Path, 'write_bytes', side_effect=OSError):
                button.click()
            self.assertIn('Unable to save', self.window.action_status.text())

    def test_long_content_small_window_and_plain_text(self):
        self.render('assignment', {'title': '<b>Homework</b>', 'instructions_summary': 'A long explanation. ' * 1000})
        self.window.resize(440, 320)
        APP.processEvents()
        self.assertLessEqual(self.window.height(), 320)
        self.assertGreater(self.window.structured.verticalScrollBar().maximum(), 0)
        self.assertIn('<b>Homework</b>', [label.text() for label in self.window.structured.findChildren(QLabel)])
