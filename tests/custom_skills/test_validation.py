import unittest
from custom_skills.helpers import sample, Field
from src.skills.custom.validator import validate_extraction


class ValidationTests(unittest.TestCase):
    def test_missing_optional_fields_and_invalid_required_value(self):
        data, warnings = validate_extraction(sample(), {'merchant': 42, 'unknown': 'discard'})
        self.assertEqual(data, {'merchant': None, 'total': None})
        self.assertEqual(warnings, ['Required field "Merchant" was not detected.'])

    def test_required_false_and_zero_are_present(self):
        skill = sample(fields=[Field('available', 'Available', 'boolean', True), Field('amount', 'Amount', 'number', True)])
        data, warnings = validate_extraction(skill, {'available': False, 'amount': 0})
        self.assertEqual(data, {'available': False, 'amount': 0})
        self.assertEqual(warnings, [])
