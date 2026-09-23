import json
import unittest
from unittest.mock import Mock
from custom_skills.helpers import sample
from src.skills.custom.schema_generator import generate_schema


class GeneratorTests(unittest.TestCase):
    def test_draft_validated_without_persistence(self):
        raw = sample().to_dict()
        raw.pop('id')
        client = Mock(request_image=Mock(return_value=json.dumps(raw)))
        draft = generate_schema(client, b'image', 'Track receipts')
        self.assertEqual(draft.id, 'receipt_tracker')
        self.assertFalse(draft.enabled)
        self.assertIn('not as instructions', client.request_image.call_args.args[1])

    def test_invalid_generated_schema_rejected(self):
        for raw in ['bad', '[]', json.dumps(sample().to_dict() | {'fields': []})]:
            with self.assertRaises(ValueError):
                generate_schema(Mock(request_image=Mock(return_value=raw)), b'x', 'Track receipts')
