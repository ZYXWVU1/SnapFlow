"""Safe, one-time migration from the Phase 1–7 source and data locations."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from src.app_version import APP_VERSION
from src.database import backup_sqlite_database
from src.paths import AppPaths


MIGRATION_VERSION = 1
_LEGACY_DATA_FILES = (
    ("custom_skills.json", "custom_skills.json"),
    ("workflows.json", "workflows.json"),
    ("workflow_history.json", "workflow_history.json"),
    ("integrations.json", "integrations.json"),
    ("learning.sqlite3", "learning.sqlite3"),
)


class MigrationError(RuntimeError):
    """Raised when safe migration cannot proceed without risking user data."""


class MigrationConflictError(MigrationError):
    """Raised when old and new data exist at the same time."""

    def __init__(self, conflicts):
        self.conflicts = tuple(Path(path) for path in conflicts)
        super().__init__("Migration stopped because these destination paths already exist: " +
                         ", ".join(str(path) for path in self.conflicts))


@dataclass(frozen=True)
class MigrationResult:
    migrated: bool
    backup_path: Path | None
    item_count: int


def _sha256(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class MigrationCoordinator:
    """Back up then copy known legacy data, never overwriting a new destination."""

    def __init__(self, paths=None):
        self.paths = paths or AppPaths()

    def _read_marker(self):
        marker = self.paths.migration_marker
        if marker.is_symlink():
            raise MigrationError("The data migration marker is a symbolic link; existing files were left untouched.")
        if not marker.exists():
            return None
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MigrationError("The data migration marker is damaged; existing files were left untouched.") from exc
        if (not isinstance(value, dict) or type(value.get("version")) is not int or
                value["version"] != MIGRATION_VERSION):
            raise MigrationError("The data migration marker has an unsupported version.")
        backup_name = value.get("backup")
        if backup_name is not None and (
                not isinstance(backup_name, str) or not backup_name or
                Path(backup_name).name != backup_name or "/" in backup_name or "\\" in backup_name):
            raise MigrationError("The data migration marker contains an unsafe backup path.")
        backup = self.paths.backup_dir / backup_name if backup_name else None
        return MigrationResult(False, backup, 0)

    def _sources(self):
        items = []
        legacy_config = self.paths.legacy_config_file
        if legacy_config.is_symlink():
            raise MigrationError("The legacy settings path is a symbolic link; migration stopped safely.")
        if legacy_config.exists():
            items.append((legacy_config, self.paths.config_file, "config.json", "file"))
        for old_name, new_name in _LEGACY_DATA_FILES:
            source = self.paths.legacy_data_dir / old_name
            destination = self.paths.data_dir / new_name
            if source.is_symlink():
                raise MigrationError(f"The legacy {old_name} path is a symbolic link; migration stopped safely.")
            if source.exists():
                items.append((source, destination, new_name, "sqlite" if old_name.endswith(".sqlite3") else "file"))

        images = self.paths.legacy_data_dir / "verified_images"
        if images.is_symlink():
            raise MigrationError("The legacy verified_images path is a symbolic link.")
        if images.exists():
            if not images.is_dir():
                raise MigrationError("The legacy verified_images path is not a regular directory.")
            files = sorted(images.rglob("*"))
            if any(path.is_symlink() for path in files):
                raise MigrationError("A symbolic link was found in the legacy examples folder; migration stopped safely.")
            if any(path.is_file() for path in files):
                items.append((images, self.paths.verified_images_dir, "verified_images", "directory"))

        for source, _, _, kind in items:
            if kind == "directory":
                continue
            if source.is_symlink() or not source.is_file():
                raise MigrationError(f"Legacy data path is not a regular file: {source.name}")
        return items

    def _backup_path(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.paths.backup_dir / f"pre-migration-{stamp}-{uuid.uuid4().hex[:8]}.zip"

    def _create_backup(self, items):
        if self.paths.backup_dir.is_symlink():
            raise MigrationError("The backup folder is a symbolic link; migration stopped safely.")
        self.paths.backup_dir.mkdir(parents=True, exist_ok=True)
        destination = self._backup_path()
        temporary = destination.with_suffix(".tmp")
        try:
            with tempfile.TemporaryDirectory(prefix="snapflow-migration-") as temp_dir:
                staged_db = Path(temp_dir) / "learning.sqlite3"
                manifest_files = []
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for source, _, archive_name, kind in items:
                        if kind == "directory":
                            for image in sorted(source.rglob("*")):
                                if not image.is_file():
                                    continue
                                relative = image.relative_to(source).as_posix()
                                name = f"verified_images/{relative}"
                                archive.write(image, name)
                                digest, size = _sha256(image)
                                manifest_files.append({"path": name, "sha256": digest, "size": size})
                        else:
                            payload = source
                            name = archive_name
                            if kind == "sqlite":
                                backup_sqlite_database(source, staged_db)
                                payload = staged_db
                            archive.write(payload, name)
                            digest, size = _sha256(payload)
                            manifest_files.append({"path": name, "sha256": digest, "size": size})
                    manifest = {
                        "format": "snapflow-pre-migration",
                        "format_version": 1,
                        "application_version": APP_VERSION,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "files": manifest_files,
                    }
                    archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            self.verify_backup(temporary)
            os.replace(temporary, destination)
            return destination
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"Unable to create a verified pre-migration backup: {exc}") from exc

    @staticmethod
    def verify_backup(path):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if names.count("manifest.json") != 1 or len(names) != len(set(names)):
                    raise MigrationError("Migration backup is missing its manifest.")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("format") != "snapflow-pre-migration" or manifest.get("format_version") != 1:
                    raise MigrationError("Migration backup manifest is unsupported.")
                entries = manifest.get("files")
                if not isinstance(entries, list):
                    raise MigrationError("Migration backup manifest has invalid file records.")
                expected = {}
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise MigrationError("Migration backup manifest has invalid file records.")
                    name, digest, size = entry.get("path"), entry.get("sha256"), entry.get("size")
                    if (not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64 or
                            any(char not in "0123456789abcdef" for char in digest) or
                            type(size) is not int or size < 0 or name in expected):
                        raise MigrationError("Migration backup manifest has invalid file records.")
                    expected[name] = entry
                if set(expected) != set(names) - {"manifest.json"}:
                    raise MigrationError("Migration backup file list does not match its manifest.")
                for name, entry in expected.items():
                    pure = PurePosixPath(name)
                    if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                        raise MigrationError("Migration backup contains an unsafe path.")
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(name) as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                            size += len(chunk)
                    if digest.hexdigest() != entry.get("sha256") or size != entry.get("size"):
                        raise MigrationError(f"Migration backup verification failed for {name}.")
        except (OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"Unable to verify the migration backup: {exc}") from exc
        return True

    def _stage_item(self, source, stage_root, relative_name, kind):
        destination = stage_root / relative_name
        if kind == "directory":
            destination.mkdir(parents=True)
            for item in sorted(source.rglob("*")):
                if item.is_dir():
                    (destination / item.relative_to(source)).mkdir(parents=True, exist_ok=True)
                elif item.is_file():
                    target = destination / item.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        elif kind == "sqlite":
            backup_sqlite_database(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if kind == "sqlite":
            db = sqlite3.connect(destination)
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise MigrationError(f"Staged database verification failed for {source.name}.")
            finally:
                db.close()
        elif kind == "directory":
            for item in source.rglob("*"):
                if item.is_file():
                    copied = destination / item.relative_to(source)
                    if _sha256(item) != _sha256(copied):
                        raise MigrationError(f"Staged example verification failed for {item.name}.")
        elif _sha256(source) != _sha256(destination):
            raise MigrationError(f"Staged file verification failed for {source.name}.")
        return destination

    def _write_marker(self, backup_path):
        marker = self.paths.migration_marker
        if marker.is_symlink():
            raise MigrationError("The data migration marker is a symbolic link.")
        temporary = marker.with_name(marker.name + ".tmp")
        payload = {"version": MIGRATION_VERSION,
                   "completed_at": datetime.now(timezone.utc).isoformat(),
                   "backup": backup_path.name if backup_path else None}
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, marker)
        finally:
            temporary.unlink(missing_ok=True)

    def run(self):
        previous = self._read_marker()
        if previous is not None:
            return previous
        if self.paths.backup_dir.is_symlink():
            raise MigrationError("The backup folder is a symbolic link; migration stopped safely.")

        items = self._sources()
        conflicts = [destination for _, destination, _, _ in items
                     if destination.exists() or destination.is_symlink()]
        if conflicts:
            raise MigrationConflictError(conflicts)

        backup_path = self._create_backup(items) if items else None
        stage_root = self.paths.data_dir / (".migration-" + uuid.uuid4().hex)
        installed = []
        try:
            stage_root.mkdir(parents=True)
            staged = []
            for source, target, archive_name, kind in items:
                relative = "verified_images" if kind == "directory" else target.name
                staged.append((self._stage_item(source, stage_root, relative, kind), target))
            for staged_path, target in staged:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_path, target)
                installed.append(target)
            self._write_marker(backup_path)
        except Exception as exc:
            for target in reversed(installed):
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"Unable to finish legacy data migration: {exc}") from exc
        finally:
            if stage_root.exists():
                shutil.rmtree(stage_root, ignore_errors=True)

        return MigrationResult(bool(items), backup_path, len(items))
