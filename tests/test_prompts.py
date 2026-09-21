import unittest
from src.prompts import MODES, get_prompt


class PromptTests(unittest.TestCase):
    def test_extract_specifies_flat_code_example(self):
        self.assertIn('"content_type": "code", "language": "python", "code":', get_prompt('extract'))
        self.assertIn('top level', get_prompt('extract'))
    def test_all_modes_have_distinct_prompts(self):
        self.assertEqual(len({get_prompt(mode) for mode in MODES}), 5)

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            get_prompt("unknown")
