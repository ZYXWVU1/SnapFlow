import unittest
from dataclasses import replace
from custom_skills.helpers import sample, Field, Definition
from src.skills.custom.models import safe_id, unique_id


class ModelTests(unittest.TestCase):
    def test_roundtrip_and_stable_id(self):
        skill = sample()
        self.assertEqual(Definition.from_dict(skill.to_dict()), skill)
        self.assertEqual(replace(skill.fields[0], label='Shop').id, 'merchant')
        self.assertEqual(safe_id('Order ID!'), 'order_id')
        self.assertEqual(unique_id('receipt_tracker', {'receipt_tracker'}), 'receipt_tracker_2')

    def test_invalid_definitions(self):
        for changes in [dict(name=''), dict(id='table'), dict(id='../bad'), dict(fields=[]),
                        dict(fields=[Field('x', 'X', 'string')] * 2), dict(enabled='yes'), dict(version=True),
                        dict(actions=['shell']), dict(detection_prompt='x' * 1001),
                        dict(fields=[Field(f'f{i}', 'X', 'string') for i in range(21)])]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                sample(**changes)

    def test_invalid_fields(self):
        for args in [('x', 'X', 'object'), ('bad id', 'X', 'string')]:
            with self.assertRaises(ValueError):
                Field(*args)

    def test_untrusted_shapes(self):
        for raw in [[], {}, {'fields': None}, sample().to_dict() | {'fields': [42]},
                    sample().to_dict() | {'actions': 'copy_json'}]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Definition.from_dict(raw)
