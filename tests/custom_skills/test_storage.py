import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from custom_skills.helpers import sample
from src.skills.custom.storage import CustomSkillStorage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'custom_skills.json'
        self.store = CustomSkillStorage(self.path)

    def test_crud_restart_enable(self):
        self.assertEqual(self.store.list_skills(), [])
        self.store.create_skill(sample())
        with self.assertRaises(ValueError):
            self.store.create_skill(sample())
        self.store.save_skill(replace(sample(), name='Updated'))
        self.store.set_enabled(sample().id, False)
        loaded = CustomSkillStorage(self.path)
        self.assertEqual(loaded.get_skill(sample().id).name, 'Updated')
        self.assertFalse(loaded.get_skill(sample().id).enabled)
        loaded.set_enabled(sample().id, True)
        loaded.delete_skill(sample().id)
        self.assertEqual(CustomSkillStorage(self.path).list_skills(), [])

    def test_corrupt_file_preserved_on_recovery(self):
        self.path.write_text('{broken')
        store = CustomSkillStorage(self.path)
        self.assertTrue(store.warning)
        self.assertEqual(store.list_skills(), [])
        store.create_skill(sample())
        self.assertTrue(any(p.read_text() == '{broken' for p in self.path.parent.glob('*.bak')))
        self.assertEqual(len(CustomSkillStorage(self.path).list_skills()), 1)

    def test_failed_write_does_not_mutate_memory_or_original(self):
        self.store.create_skill(sample())
        original = self.path.read_bytes()
        with patch('src.skills.custom.storage.os.replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                self.store.delete_skill(sample().id)
        self.assertIsNotNone(self.store.get_skill(sample().id))
        self.assertEqual(self.path.read_bytes(), original)

    def test_bad_entry_keeps_valid_entries_and_unknown_version_readonly(self):
        self.path.write_text(json.dumps({'version': 1, 'skills': [sample().to_dict(), {}]}))
        store = CustomSkillStorage(self.path)
        self.assertEqual(len(store.list_skills()), 1)
        self.assertTrue(store.warning)
        self.path.write_text('{"version": 99, "skills": []}')
        store = CustomSkillStorage(self.path)
        with self.assertRaises(ValueError):
            store.create_skill(sample())
