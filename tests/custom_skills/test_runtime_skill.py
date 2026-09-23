import csv
import io
import json
import unittest
from custom_skills.helpers import sample, Field
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.modes import ResponseFormatError
from src.skill_actions import execute_action


class RuntimeTests(unittest.TestCase):
    def test_partial_required_extra_and_fences(self):
        result = RuntimeCustomSkill(sample()).parse('```json\n{"total":12.5,"extra":"ignored"}\n```', .9)
        self.assertEqual(result.data, {'merchant': None, 'total': 12.5})
        self.assertIn('Merchant', result.warnings[0])
        self.assertEqual(result.presentation, (('merchant', 'Merchant', True), ('total', 'Total paid', False)))
        self.assertIn('Receipt Tracker', result.text)

    def test_invalid_values_and_negative_numbers(self):
        skill = RuntimeCustomSkill(sample())
        self.assertIsNone(skill.parse('{"total":true}').data['total'])
        self.assertEqual(skill.parse('{"total":-5}').data['total'], -5)
        for raw in ('[]', 'broken', '{"total":NaN}'):
            with self.assertRaises(ResponseFormatError):
                skill.parse(raw)

    def test_all_types(self):
        cases = [('string', 'hello', 3), ('multiline_text', 'a\nb', []), ('number', 2.5, '2.5'),
                 ('date', '2026-09-22', '2026-02-30'), ('time', '14:05', '2 PM'),
                 ('datetime', '2026-09-22T14:05:00', '2026-09-22'), ('boolean', False, 0),
                 ('url', 'https://example.com', 'javascript:alert(1)'), ('email', 'a@example.com', 'bad'),
                 ('list_string', ['a', 'b'], ['a', 1])]
        for kind, good, bad in cases:
            skill = RuntimeCustomSkill(sample(fields=[Field('value', 'Value', kind)]))
            with self.subTest(kind=kind):
                self.assertEqual(skill.parse(json.dumps({'value': good})).data['value'], good)
                self.assertIsNone(skill.parse(json.dumps({'value': bad})).data['value'])

    def test_actions_use_labels_and_csv_quoting(self):
        result = RuntimeCustomSkill(sample()).parse('{"merchant":"Shop, Inc","total":12}')
        rows = list(csv.reader(io.StringIO(execute_action('save_csv', result).payload)))
        self.assertEqual(rows, [['Merchant', 'Total paid'], ['Shop, Inc', '12']])
        self.assertIn('Total paid', execute_action('copy_text', result).payload)
        self.assertIn('**Total paid:** 12', execute_action('copy_markdown', result).payload)

    def test_csv_neutralizes_formula_text_but_preserves_numbers(self):
        skill = RuntimeCustomSkill(sample(fields=[Field('merchant', '=Label', 'string'), Field('total', 'Total', 'number')]))
        result = skill.parse('{"merchant":"  =1+1","total":-5}')
        rows = list(csv.reader(io.StringIO(execute_action('save_csv', result).payload)))
        self.assertEqual(rows, [["'=Label", 'Total'], ["'  =1+1", '-5']])
