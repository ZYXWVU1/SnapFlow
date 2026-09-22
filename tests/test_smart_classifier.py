import json
import unittest
from unittest.mock import Mock

from src.smart.classifier import ScreenshotClassifier, parse_classification_response
from src.smart.prompts import CLASSIFICATION_PROMPT


class ClassifierTests(unittest.TestCase):
    def test_valid_and_fenced_json(self):
        raw = json.dumps(dict(content_type='code_error', confidence=.96, reasoning='Traceback visible.'))
        for response in (raw, '```json\n' + raw + '\n```'):
            result = parse_classification_response(response)
            self.assertEqual(result.content_type, 'code_error')
            self.assertEqual(result.confidence, .96)

    def test_invalid_responses_are_unknown(self):
        for raw in ('hello', '{"type":"banana"}', '{}', '[]', 'null',
                    '{"content_type":"table","confidence":3.5}',
                    '{"content_type":"table","confidence":-0.1}',
                    '{"content_type":"table","confidence":true}',
                    '{"content_type":"table","confidence":"0.9"}',
                    '{"content_type":"table","confidence":NaN}',
                    '{"content_type":"table","confidence":1e999}',
                    '{"content_type":[],"confidence":0.9}',
                    '{"content_type":"banana","confidence":0.9}',
                    '{"content_type":"table","confidence":0.9,"reasoning":[]}',
                    '{"content_type":"table","confidence":0.9,"metadata":[]}'):
            with self.subTest(raw=raw):
                result = parse_classification_response(raw)
                self.assertEqual((result.content_type, result.confidence), ('unknown', 0))

    def test_single_request_reuses_transport_and_image(self):
        client = Mock()
        client.request_image.return_value = '{"content_type":"table","confidence":0.8}'
        result = ScreenshotClassifier(client).classify(b'original png')
        self.assertEqual(result.content_type, 'table')
        client.request_image.assert_called_once_with(b'original png', CLASSIFICATION_PROMPT)

    def test_api_failure_is_redacted_and_falls_back(self):
        client = Mock()
        client.request_image.side_effect = RuntimeError('secret screenshot text')
        with self.assertLogs('src.smart', level='WARNING') as logs:
            result = ScreenshotClassifier(client).classify(b'png')
        self.assertEqual(result.content_type, 'unknown')
        self.assertNotIn('secret screenshot text', str(logs.output))
