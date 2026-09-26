import tempfile
import unittest
from pathlib import Path
from src.config import Config, load_config, parse_hotkey, save_config


class ConfigTests(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            self.assertEqual(load_config(path).hotkey, "ctrl+shift+s")
            config = Config("ctrl+alt+a", "debug", 1600, False)
            save_config(config, path)
            self.assertEqual(load_config(path), config)
            self.assertNotIn("api_key", path.read_text())

    def test_corrupt_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for content in ('{', '[]', '{"default_mode":"oops"}', '{"always_on_top":"false"}', '{"max_image_width":true}', '{"api_key":"secret"}'):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_hotkeys(self):
        self.assertEqual(parse_hotkey("Ctrl + Shift + S"), (6, ord("S")))
        for value in ("s", "ctrl+ctrl+s", "ctrl+é", "ctrl+space", "meta+s"):
            with self.assertRaises(ValueError):
                parse_hotkey(value)

    def test_theme_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = Config(theme='dark')
            save_config(config, path)
            self.assertEqual(load_config(path).theme, 'dark')
            self.assertEqual(Config().theme, 'system')
            with self.assertRaises(ValueError):
                Config(theme='sepia')
