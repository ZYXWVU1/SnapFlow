import unittest
from custom_skills.helpers import sample, Field
from src.skills.custom.prompt_builder import extraction_prompt, matcher_prompt


class PromptTests(unittest.TestCase):
    def test_controlled_prompts(self):
        skill = sample(fields=[Field('day', 'Date', 'date')])
        prompt = extraction_prompt(skill)
        self.assertIn('YYYY-MM-DD', prompt)
        self.assertIn('Never invent', prompt)
        self.assertIn('not as instructions', prompt)
        matcher = matcher_prompt([skill])
        self.assertIn('receipt_tracker', matcher)
        self.assertNotIn('"fields"', matcher)
        self.assertIn('not as instructions', matcher)
