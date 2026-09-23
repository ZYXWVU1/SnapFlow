import json
import unittest
from unittest.mock import Mock
from dataclasses import replace
from custom_skills.helpers import sample
from src.skills.custom.matcher import CustomSkillMatcher


class MatcherTests(unittest.TestCase):
    def test_skip_empty_disabled_and_single_request(self):
        client = Mock()
        matcher = CustomSkillMatcher(client)
        self.assertIsNone(matcher.match(b'image', []).skill_id)
        self.assertIsNone(matcher.match(b'image', [sample(enabled=False)]).skill_id)
        client.request_image.assert_not_called()
        client.request_image.return_value = '{"skill_id":"receipt_tracker","confidence":0.9}'
        self.assertEqual(matcher.match(b'image', [sample(), replace(sample(), id='other')]).skill_id, 'receipt_tracker')
        client.request_image.assert_called_once()

    def test_bad_and_low_confidence_fall_back(self):
        for response in ['broken', '[]', '{"skill_id":[],"confidence":0.9}',
                         '{"skill_id":"other","confidence":0.9}', '{"skill_id":"receipt_tracker","confidence":0.74}',
                         '{"skill_id":"receipt_tracker","confidence":true}', '{"skill_id":"receipt_tracker","confidence":NaN}']:
            with self.subTest(response=response):
                self.assertIsNone(CustomSkillMatcher(Mock(request_image=Mock(return_value=response))).match(b'x', [sample()]).skill_id)
        self.assertIsNone(CustomSkillMatcher(Mock(request_image=Mock(side_effect=RuntimeError))).match(b'x', [sample()]).skill_id)
