"""Central paths for bundled resources and writable SnapFlow user data."""
import os
from pathlib import Path, PurePath
import sys

from src.app_version import APP_NAME


SOURCE_ROOT = Path(__file__).resolve().parent.parent


class AppPaths:
    """Resolve application resources and per-user writable paths.

    ``resource_root`` and ``data_dir`` can be injected by tests and tools. The
    default resource root supports PyInstaller's one-directory ``_MEIPASS``
    extraction directory; the default data root is stable across launch paths.
    """

    def __init__(self, *, resource_root=None, data_dir=None, legacy_data_dir=None):
        bundled_root = getattr(sys, "_MEIPASS", SOURCE_ROOT)
        self._resource_root = Path(resource_root or bundled_root).expanduser()
        self._data_dir = Path(data_dir).expanduser() if data_dir is not None else self._default_data_dir()
        self._legacy_data_dir = (Path(legacy_data_dir).expanduser() if legacy_data_dir is not None
                                 else self._default_legacy_data_dir())

    @staticmethod
    def _roaming_root():
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata).expanduser()
        if os.name == "nt":
            return Path.home() / "AppData" / "Roaming"
        xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        return Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"

    @classmethod
    def _default_data_dir(cls):
        return cls._roaming_root() / APP_NAME

    @classmethod
    def _default_legacy_data_dir(cls):
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            root = Path(appdata).expanduser()
        elif os.name == "nt":
            root = Path.home() / "AppData" / "Roaming"
        else:
            root = Path.home() / ".local" / "share"
        return root / "AI Screenshot Helper"

    @property
    def resource_root(self):
        return self._resource_root

    @property
    def data_dir(self):
        return self._data_dir

    @property
    def legacy_data_dir(self):
        return self._legacy_data_dir

    @property
    def config_dir(self):
        return self.data_dir

    @property
    def cache_dir(self):
        return self.data_dir / "cache"

    @property
    def log_dir(self):
        return self.data_dir / "logs"

    @property
    def backup_dir(self):
        return self.data_dir / "backups"

    @property
    def config_file(self):
        return self.config_dir / "config.json"

    @property
    def environment_file(self):
        """The source/development .env path; it is never part of user backups."""
        return self.resource_root / ".env"

    @property
    def legacy_config_file(self):
        return self.resource_root / "config.json"

    @property
    def custom_skills_file(self):
        return self.data_dir / "custom_skills.json"

    @property
    def workflows_file(self):
        return self.data_dir / "workflows.json"

    @property
    def workflow_history_file(self):
        return self.data_dir / "workflow_history.json"

    @property
    def integrations_file(self):
        return self.data_dir / "integrations.json"

    @property
    def learning_database(self):
        return self.data_dir / "learning.sqlite3"

    @property
    def verified_images_dir(self):
        return self.data_dir / "verified_images"

    @property
    def migration_marker(self):
        return self.data_dir / "migration.json"

    def resource_path(self, relative_path):
        path = PurePath(relative_path)
        if path.is_absolute() or path.drive or any(part == ".." for part in path.parts):
            raise ValueError("Resource path must stay inside the application resource directory.")
        return self.resource_root.joinpath(*path.parts)
