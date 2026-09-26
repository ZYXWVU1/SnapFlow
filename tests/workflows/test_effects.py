import tempfile
import unittest
from pathlib import Path

from src.skills.registry import SKILLS
from src.skill_actions import ACTIONS, ActionResult
from src.workflows.effects import WorkflowEffects, render_template


class EffectsTests(unittest.TestCase):
    def test_template_is_field_only_and_missing_is_blank(self):
        self.assertEqual(render_template('{title} — {course}', {'title': 'HW', 'course': None}), 'HW — ')
        with self.assertRaises(ValueError):
            render_template('{title.__class__}', {'title': 'HW'})

    def test_append_csv_creates_and_checks_headers(self):
        result = SKILLS['assignment'].parse('{"title":"HW","course":"CS"}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'assignments.csv'
            effects = WorkflowEffects()
            action = ACTIONS['append_csv']
            config = {'file_path': str(path)}
            self.assertTrue(effects.execute(action, result, config).success)
            first = path.read_bytes()
            self.assertTrue(effects.execute(action, result, config).success)
            self.assertEqual(path.read_bytes(), first + first.splitlines(keepends=True)[1])
            path.write_text('wrong,header\n')
            self.assertFalse(effects.execute(action, result, config).success)
            self.assertEqual(path.read_text(), 'wrong,header\n')

    def test_missing_destination_and_no_hidden_dirs(self):
        result = SKILLS['assignment'].parse('{"title":"HW","course":"CS"}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing' / 'assignments.csv'
            outcome = WorkflowEffects().execute(ACTIONS['append_csv'], result, {'file_path': str(path)})
            self.assertFalse(outcome.success)
            self.assertFalse(path.parent.exists())

    def test_table_append_neutralizes_formula_cells(self):
        import csv
        import io
        result = SKILLS['table'].parse('{"headers":["Name"],"rows":[["=1+1"],["+cmd"]]}')
        payload = ACTIONS['append_csv'].build(result)
        self.assertEqual(list(csv.reader(io.StringIO(payload)))[1:], [["'=1+1"], ["'+cmd"]])

    def test_template_allows_braces_in_data(self):
        self.assertEqual(render_template('{title}', {'title': 'A {B}'}), 'A {B}')

    def test_append_csv_respects_missing_final_newline(self):
        import csv
        result = SKILLS['assignment'].parse('{"title":"HW"}')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.csv'
            payload = ACTIONS['append_csv'].build(result)
            path.write_bytes(payload.rstrip('\r\n').encode('utf-8'))
            outcome = WorkflowEffects().execute(ACTIONS['append_csv'], result, {'file_path': str(path)})
            self.assertTrue(outcome.success)
            with path.open(newline='', encoding='utf-8') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows), 3)

    def test_workflow_copy_csv_is_safe_for_spreadsheets(self):
        result = SKILLS['table'].parse('{"headers":["Value"],"rows":[["=1+1"]]}')
        copied = []
        effects = WorkflowEffects(lambda kind, payload, _: (copied.append(payload) or ActionResult(True)))
        self.assertTrue(effects.execute(ACTIONS['copy_csv'], result, {}).success)
        self.assertIn("'=1+1", copied[0])
