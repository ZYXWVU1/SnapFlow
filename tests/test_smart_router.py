import unittest
from src.config import Config
from src.smart.models import ClassificationResult
from src.smart.router import SmartRouter


class RouterTests(unittest.TestCase):
    def test_all_routes_and_boundary(self):
        for kind, route in dict(code_error='debug', table='extract', assignment='assignment', event='event', unknown='ask').items():
            with self.subTest(kind=kind):
                self.assertEqual(SmartRouter().route(ClassificationResult(kind, .75)), route)
                self.assertEqual(SmartRouter().route(ClassificationResult(kind, .749)), 'ask')

    def test_invalid_results_never_control_routing(self):
        for kind, confidence in [('banana', .99), ([], .99), ('table', float('nan')), ('table', 3.5), ('table', True)]:
            self.assertEqual(SmartRouter().route(ClassificationResult(kind, confidence)), 'ask')

    def test_configured_threshold_and_validation(self):
        self.assertEqual(SmartRouter(.9).route(ClassificationResult('table', .8)), 'ask')
        self.assertEqual(Config().smart_classification_threshold, .75)
        for value in (-1, 2, True, float('nan'), '0.8'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config(smart_classification_threshold=value)
