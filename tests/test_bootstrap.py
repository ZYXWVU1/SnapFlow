import sys
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from src import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_skips_pip_when_all_dependencies_are_available(self):
        with patch("src.bootstrap._missing_modules", return_value=[]), patch(
            "src.bootstrap.subprocess.run"
        ) as run:
            bootstrap.ensure_dependencies()

        run.assert_not_called()

    def test_installs_requirements_when_a_dependency_is_missing(self):
        with patch(
            "src.bootstrap._missing_modules", side_effect=[["PySide6"], []]
        ), patch(
            "src.bootstrap.subprocess.run",
            return_value=CompletedProcess([], 0, "", ""),
        ) as run:
            bootstrap.ensure_dependencies()

        command = run.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--disable-pip-version-check", command)
        self.assertIn("-r", command)
        self.assertEqual(Path(command[command.index("-r") + 1]).name, "requirements.txt")


if __name__ == "__main__":
    unittest.main()
