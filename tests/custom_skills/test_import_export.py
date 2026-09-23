import json
import unittest
from custom_skills.helpers import sample
from src.skills.custom.import_export import export_skill, import_skill


class ImportTests(unittest.TestCase):
    def test_roundtrip_and_copy(self):
        exported = export_skill(sample())
        self.assertEqual(set(json.loads(exported)), {'format', 'version', 'skill'})
        restored, warnings = import_skill(exported, {'receipt_tracker'})
        self.assertEqual(restored.id, 'receipt_tracker_2')
        self.assertTrue(warnings)

    def test_unknown_actions_removed_with_warning(self):
        raw = json.loads(export_skill(sample()))
        raw['skill']['actions'].append('shell')
        skill, warnings = import_skill(json.dumps(raw))
        self.assertNotIn('shell', skill.actions)
        self.assertTrue(warnings)

    def test_invalid_formats_rejected(self):
        raw = json.loads(export_skill(sample()))
        for text in ['broken', '[]', json.dumps(raw | {'version': 2}), json.dumps(raw | {'format': 'other'}),
                     json.dumps(raw | {'skill': sample().to_dict() | {'fields': [{'id':'x','label':'X','type':'object'}]}})]:
            with self.assertRaises(ValueError):
                import_skill(text)
