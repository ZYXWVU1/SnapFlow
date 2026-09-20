import unittest
from src.prompts import MODES, get_prompt


class PromptTests(unittest.TestCase):
    def test_all_modes_have_distinct_prompts(self):
        self.assertEqual(len({get_prompt(mode) for mode in MODES}), 6)

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            get_prompt("unknown")
