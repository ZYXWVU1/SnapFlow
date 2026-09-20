"""Startup checks for the Python packages used by the application."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import site
import subprocess
import sys


REQUIRED_MODULES = {
    "PySide6": "PySide6",
    "dotenv": "python-dotenv",
    "openai": "openai",
}


class DependencyError(RuntimeError):
    """Raised when the application cannot install its runtime dependencies."""


def _add_user_site_to_import_path() -> None:
    """Support portable/embedded Python builds that omit the user site path."""
    try:
        user_site = site.getusersitepackages()
        if user_site and os.path.isdir(user_site):
            site.addsitedir(user_site)
    except (AttributeError, OSError):
        # Some restricted Python builds do not expose a usable user site.
        pass


def _missing_modules() -> list[str]:
    return [
        module for module in REQUIRED_MODULES if importlib.util.find_spec(module) is None
    ]


def _running_in_virtual_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or sys.prefix != getattr(sys, "real_prefix", sys.prefix)
    )


def _install_dependencies(requirements_file: Path, missing: list[str]) -> None:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--progress-bar",
        "on",
        "--timeout",
        "30",
        "--retries",
        "2",
    ]
    # A normal Python installation may be protected by Windows permissions.
    # A venv should stay isolated, so install into it instead of using --user.
    if not _running_in_virtual_environment() and getattr(site, "ENABLE_USER_SITE", False):
        command.append("--user")
    command.extend(["-r", str(requirements_file)])

    print(
        "Missing Python packages ({}). Installing them now...".format(
            ", ".join(REQUIRED_MODULES[module] for module in missing)
        ),
        file=sys.stderr,
    )
    print("Pip output will appear below. Please keep this window open.", file=sys.stderr)
    sys.stderr.flush()
    try:
        # Do not capture pip's output: PySide6 is a large download, and hiding
        # the progress bar makes a healthy install look like a frozen program.
        result = subprocess.run(command, text=True, check=False)
    except OSError as error:
        raise DependencyError(
            f"Could not start pip using this interpreter: {error}"
        ) from error
    if result.returncode == 0:
        return

    raise DependencyError(
        "Could not install the required Python packages with this interpreter."
        f"\nCommand: {' '.join(command)}"
        f"\nPip exited with code {result.returncode}."
    )


def ensure_dependencies() -> None:
    """Install missing runtime packages before any third-party import occurs."""
    _add_user_site_to_import_path()
    missing = _missing_modules()
    if not missing:
        return

    requirements_file = Path(__file__).resolve().parents[1] / "requirements.txt"
    if not requirements_file.is_file():
        raise DependencyError(f"Dependency file not found: {requirements_file}")

    _install_dependencies(requirements_file, missing)
    _add_user_site_to_import_path()
    still_missing = _missing_modules()
    if still_missing:
        names = ", ".join(REQUIRED_MODULES[module] for module in still_missing)
        raise DependencyError(
            "The installer finished, but these packages are still unavailable: " + names
        )


def show_dependency_error(error: DependencyError) -> None:
    """Show a useful error even when the app is launched by double-clicking."""
    message = str(error)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AI Screenshot Helper", 0x10)
    except (AttributeError, OSError):
        print(message, file=sys.stderr)
