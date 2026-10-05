"""Versioned, integrity-checked backups for SnapFlow-owned user data."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile

from src.app_version import APP_NAME, APP_VERSION, is_newer_version, parse_semantic_version
from src.database import CURRENT_SCHEMA_VERSION, backup_sqlite_database
from src.paths import AppPaths


BACKUP_FORMAT = "snapflow-backup"
BACKUP_FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_FILES = 10000
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024

_FIXED_MEMBERS = {
    "config/config.json": "settings",
    "config/local_models.json": "settings",
    "config/beta_features.json": "settings",
    "skills/custom_skills.json": "skills",
    "workflows/workflows.json": "workflows",
    "workflows/workflow_history.json": "workflows",
    "integrations/integrations.json": "integrations",
    "integrations/mcp_connections.json": "integrations",
    "databases/learning.sqlite3": "evaluation",
}
_CATEGORY_TARGETS = {
    "settings": ("config_file", "model_records_file", "beta_features_file"),
    "skills": ("custom_skills_file",),
    "workflows": ("workflows_file", "workflow_history_file"),
    "integrations": ("integrations_file", "mcp_connections_file"),
    "evaluation": ("learning_database",),
    "examples": ("verified_images_dir",),
    "memory": ("memory_images_dir",),
}


class BackupError(RuntimeError):
    """Raised when a backup cannot be safely created, inspected, or restored."""


@dataclass(frozen=True)
class BackupInspection:
    application_version: str
    created_at: str
    categories: tuple[str, ...]
    file_count: int
    total_size_bytes: int


@dataclass(frozen=True)
class BackupRestoreResult:
    current_backup: Path
    categories: tuple[str, ...]


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _regular_file(path):
    return Path(path).is_file() and not Path(path).is_symlink()


class BackupService:
    def __init__(self, paths=None, app_version=APP_VERSION):
        self.paths = paths or AppPaths()
        self.app_version = app_version
        try:
            parse_semantic_version(app_version)
        except ValueError as exc:
            raise ValueError("Backup service needs a valid Semantic Version.") from exc

    def _source_files(self, temporary_root):
        items = []
        fixed_sources = (
            ("settings", "config/config.json", self.paths.config_file, "file"),
            ("settings", "config/local_models.json", self.paths.model_records_file, "file"),
            ("settings", "config/beta_features.json", self.paths.beta_features_file, "file"),
            ("skills", "skills/custom_skills.json", self.paths.custom_skills_file, "file"),
            ("workflows", "workflows/workflows.json", self.paths.workflows_file, "file"),
            ("workflows", "workflows/workflow_history.json", self.paths.workflow_history_file, "file"),
            ("integrations", "integrations/integrations.json", self.paths.integrations_file, "file"),
            ("integrations", "integrations/mcp_connections.json", self.paths.mcp_connections_file, "file"),
            ("evaluation", "databases/learning.sqlite3", self.paths.learning_database, "sqlite"),
        )
        for category, member, source, kind in fixed_sources:
            if not source.exists():
                continue
            if not _regular_file(source):
                raise BackupError(f"Refusing to back up a non-regular app data file: {source.name}")
            payload = source
            if kind == "sqlite":
                payload = backup_sqlite_database(source, temporary_root / "learning.sqlite3")
            items.append((category, member, payload))

        image_dir = self.paths.verified_images_dir
        if image_dir.exists():
            if image_dir.is_symlink() or not image_dir.is_dir():
                raise BackupError("The verified examples path is not a regular directory.")
            for image in sorted(image_dir.rglob("*")):
                if image.is_symlink():
                    raise BackupError("Refusing to back up a symbolic link in verified examples.")
                if not image.is_file():
                    continue
                relative = image.relative_to(image_dir).as_posix()
                member = f"examples/verified_images/{relative}"
                if _safe_member_category(member) != "examples":
                    raise BackupError("An example filename cannot be represented safely in a backup.")
                items.append(("examples", member, image))
        memory_dir = self.paths.memory_images_dir
        if memory_dir.exists():
            if memory_dir.is_symlink() or not memory_dir.is_dir():
                raise BackupError('The Visual Memory images path is not a regular directory.')
            for image in sorted(memory_dir.iterdir()):
                if image.is_symlink():
                    raise BackupError('Refusing to back up a symbolic link in Visual Memory.')
                if image.is_file() and re.fullmatch(r'[0-9a-f]{32}(?:-thumb)?\.png', image.name):
                    items.append(('memory', f'memory/images/{image.name}', image))
        return items

    def _check_destination(self, destination):
        if destination.is_symlink():
            raise BackupError("Choose a regular backup file, not a symbolic link.")
        resolved = destination.resolve()
        protected = [
            self.paths.environment_file,
            self.paths.config_file,
            self.paths.model_records_file,
            self.paths.custom_skills_file,
            self.paths.workflows_file,
            self.paths.workflow_history_file,
            self.paths.integrations_file,
            self.paths.learning_database,
            self.paths.legacy_data_dir / "custom_skills.json",
            self.paths.legacy_data_dir / "workflows.json",
            self.paths.legacy_data_dir / "workflow_history.json",
            self.paths.legacy_data_dir / "integrations.json",
            self.paths.legacy_data_dir / "learning.sqlite3",
        ]
        if any(resolved == item.resolve() for item in protected):
            raise BackupError("Choose a backup filename outside active application data files.")
        try:
            resolved.relative_to(self.paths.legacy_data_dir.resolve())
            raise BackupError("Backups cannot be written into the legacy application data folder.")
        except ValueError:
            pass
        try:
            resolved.relative_to(self.paths.resource_root.resolve())
            raise BackupError("Backups cannot be written into the read-only application resource folder.")
        except ValueError:
            pass
        try:
            resolved.relative_to(self.paths.data_dir.resolve())
            under_data = True
        except ValueError:
            under_data = False
        if under_data:
            try:
                resolved.relative_to(self.paths.backup_dir.resolve())
            except ValueError as exc:
                raise BackupError("Choose a backup under the backup folder or outside SnapFlow's data folder.") from exc

    def export_backup(self, destination):
        destination = Path(destination).expanduser()
        self._check_destination(destination)
        if destination.exists() and destination.is_dir():
            raise BackupError("Backup destination must be a filename.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_archive = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="snapflow-export-", dir=self.paths.cache_dir) as work:
                items = self._source_files(Path(work))
                records = []
                with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for category, member, source in items:
                        archive.write(source, member)
                        digest, size = _hash_file(source)
                        records.append({"path": member, "sha256": digest, "size": size})
                    manifest = {
                        "format": BACKUP_FORMAT,
                        "format_version": BACKUP_FORMAT_VERSION,
                        "application_name": APP_NAME,
                        "application_version": self.app_version,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "included_categories": sorted({category for category, _, _ in items}),
                        "files": records,
                    }
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            self.inspect_backup(temporary_archive)
            os.replace(temporary_archive, destination)
            return destination
        except BackupError:
            temporary_archive.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
            temporary_archive.unlink(missing_ok=True)
            raise BackupError(f"Unable to create the backup: {exc}") from exc

    @staticmethod
    def _category_for_path(member):
        if member in _FIXED_MEMBERS:
            return _FIXED_MEMBERS[member]
        return _safe_member_category(member)

    def _stage_and_validate(self, archive_path, stage_root):
        archive_path = Path(archive_path)
        if not _regular_file(archive_path):
            raise BackupError("Select a regular backup ZIP file.")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(names) != len(set(names)) or names.count("manifest.json") != 1:
                    raise BackupError("Backup contains duplicate entries or an invalid manifest count.")
                if len(infos) > MAX_FILES + 1:
                    raise BackupError("Backup contains too many files.")
                manifest_info = next(info for info in infos if info.filename == "manifest.json")
                if manifest_info.is_dir() or manifest_info.file_size > MAX_MANIFEST_BYTES:
                    raise BackupError("Backup manifest is too large or invalid.")
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise BackupError("Backup manifest must be a JSON object.")
                if manifest.get("format") != BACKUP_FORMAT or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                    raise BackupError("This backup format is not supported.")
                if manifest.get("application_name") != APP_NAME:
                    raise BackupError("This archive is not a SnapFlow backup.")
                app_version = manifest.get("application_version")
                try:
                    parse_semantic_version(app_version)
                except ValueError as exc:
                    raise BackupError("Backup contains an invalid application version.") from exc
                if is_newer_version(app_version, self.app_version):
                    raise BackupError("This backup was created by a newer SnapFlow version.")
                created_at = manifest.get("created_at")
                if not isinstance(created_at, str):
                    raise BackupError("Backup creation time is missing.")
                try:
                    datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise BackupError("Backup creation time is invalid.") from exc

                categories = manifest.get("included_categories")
                records = manifest.get("files")
                if (not isinstance(categories, list) or not isinstance(records, list) or
                        len(records) > MAX_FILES or any(not isinstance(value, str) for value in categories) or
                        len(categories) != len(set(categories)) or set(categories) - set(_CATEGORY_TARGETS)):
                    raise BackupError("Backup manifest data categories are invalid.")

                expected = {}
                for record in records:
                    if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
                        raise BackupError("Backup manifest file records are invalid.")
                    member, digest, size = record.get("path"), record.get("sha256"), record.get("size")
                    category = self._category_for_path(member) if isinstance(member, str) else None
                    if (category is None or not isinstance(digest, str) or len(digest) != 64 or
                            any(char not in "0123456789abcdef" for char in digest) or
                            type(size) is not int or size < 0 or size > MAX_FILE_BYTES or member in expected):
                        raise BackupError("Backup manifest file records are invalid.")
                    expected[member] = (digest, size, category)
                derived_categories = {record[2] for record in expected.values()}
                if set(categories) != derived_categories:
                    raise BackupError("Backup categories do not match the files it contains.")
                if set(names) != set(expected) | {"manifest.json"}:
                    raise BackupError("Backup contents do not match its manifest.")
                if sum(record[1] for record in expected.values()) > MAX_TOTAL_BYTES:
                    raise BackupError("Backup expands beyond the allowed size.")

                stage_root.mkdir(parents=True, exist_ok=True)
                infos_by_name = {info.filename: info for info in infos}
                total = 0
                for member, (expected_digest, expected_size, _) in expected.items():
                    info = infos_by_name[member]
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if info.is_dir() or stat.S_ISLNK(mode) or info.file_size != expected_size:
                        raise BackupError("Backup contains a directory, symbolic link, or inconsistent file size.")
                    category = self._category_for_path(member)
                    pure = PurePosixPath(member)
                    if pure.is_absolute() or ".." in pure.parts or "\\" in member:
                        raise BackupError("Backup contains an unsafe path.")
                    target = stage_root.joinpath(*pure.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(info, "r") as source, target.open("xb") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(chunk)
                            total += len(chunk)
                            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                                raise BackupError("Backup expands beyond the allowed size.")
                            digest.update(chunk)
                            output.write(chunk)
                    if size != expected_size or digest.hexdigest() != expected_digest:
                        raise BackupError(f"Backup integrity check failed for {member}.")

                for member, (_, size, _) in expected.items():
                    if member.endswith(".json"):
                        if size > MAX_JSON_BYTES:
                            raise BackupError(f"Backup JSON data is too large to validate: {member}.")
                        self._validate_json_file(member, stage_root.joinpath(*PurePosixPath(member).parts))

                database_stage = stage_root / "databases" / "learning.sqlite3"
                if database_stage.exists():
                    connection = sqlite3.connect(database_stage)
                    try:
                        integrity = connection.execute("PRAGMA integrity_check").fetchone()
                        version = connection.execute("PRAGMA user_version").fetchone()[0]
                        if not integrity or integrity[0] != "ok":
                            raise BackupError("The learning database inside this backup is damaged.")
                        if version > CURRENT_SCHEMA_VERSION:
                            raise BackupError("The learning database needs a newer SnapFlow version.")
                    finally:
                        connection.close()

                return BackupInspection(app_version, created_at, tuple(sorted(categories)),
                                        len(records), total)
        except BackupError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError,
                ValueError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile,
                sqlite3.Error) as exc:
            raise BackupError(f"Unable to validate this backup: {exc}") from exc

    @staticmethod
    def _validate_json_file(member, path):
        try:
            if member == "config/local_models.json" and Path(path).stat().st_size > 4 * 1024 ** 2:
                raise ValueError("Local model registry exceeds its size limit.")
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if member == "config/config.json":
                from src.config import Config
                if not isinstance(data, dict):
                    raise ValueError("Settings must be a JSON object.")
                Config(**data)
            elif member == "config/local_models.json":
                _validate_local_model_registry(data)
            elif member == 'config/beta_features.json':
                from src.beta.flags import BetaFeatureService
                if Path(path).stat().st_size > 16384:
                    raise ValueError('Oversized beta settings.')
                service = BetaFeatureService(AppPaths(data_dir=Path(path).parent))
                if service.warning:
                    raise ValueError(service.warning)
            elif member == 'integrations/mcp_connections.json':
                from src.mcp.client.storage import MCPStorage
                from copy import deepcopy
                if Path(path).stat().st_size > 2 * 1024 ** 2:
                    raise ValueError('Oversized MCP metadata.')
                # Archived metadata must not probe another machine's filesystem.
                inert = deepcopy(data)
                if not isinstance(inert, dict) or not isinstance(inert.get('profiles'), list):
                    raise ValueError('Invalid MCP metadata.')
                for profile in inert['profiles']:
                    if not isinstance(profile, dict):
                        raise ValueError('Invalid MCP profile.')
                    working = profile.get('working_directory')
                    if working is not None:
                        if (not isinstance(working, str) or len(working) > 2048 or '\x00' in working or
                                not (PureWindowsPath(working).is_absolute() or PurePosixPath(working).is_absolute())):
                            raise ValueError('Invalid archived MCP working directory.')
                        profile['working_directory'] = None
                MCPStorage._validate(inert)
            elif member == "skills/custom_skills.json":
                from src.skills.custom.models import CustomSkillDefinition
                if (not isinstance(data, dict) or type(data.get("version")) is not int or
                        data["version"] != 1 or not isinstance(data.get("skills"), list)):
                    raise ValueError("Custom Skills data has an unsupported structure.")
                skills = [CustomSkillDefinition.from_dict(item) for item in data["skills"]]
                if len({skill.id for skill in skills}) != len(skills):
                    raise ValueError("Custom Skills data contains duplicate IDs.")
            elif member == "workflows/workflows.json":
                from src.workflows.models import WorkflowDefinition
                if (not isinstance(data, dict) or type(data.get("version")) is not int or
                        data["version"] != 1 or not isinstance(data.get("workflows"), list)):
                    raise ValueError("Workflow data has an unsupported structure.")
                workflows = [WorkflowDefinition.from_dict(item) for item in data["workflows"]]
                if len({workflow.id for workflow in workflows}) != len(workflows):
                    raise ValueError("Workflow data contains duplicate IDs.")
            elif member == "workflows/workflow_history.json":
                from src.workflows.history import WorkflowHistory
                history = WorkflowHistory(path)
                if history.warning:
                    raise ValueError(history.warning)
            elif member == "integrations/integrations.json":
                from src.integrations.storage import ConnectionStorage
                connections = ConnectionStorage(path)
                if connections.warning:
                    raise ValueError(connections.warning)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError,
                ValueError, OverflowError, RecursionError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Backup contains invalid application data in {member}: {exc}") from exc

    def inspect_backup(self, archive_path):
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="snapflow-inspect-", dir=self.paths.cache_dir) as work:
                return self._stage_and_validate(archive_path, Path(work) / "files")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(f"Unable to inspect the backup: {exc}") from exc

    def _target_for_member(self, member):
        if member == "config/config.json":
            return self.paths.config_file
        if member == "config/local_models.json":
            return self.paths.model_records_file
        if member == 'config/beta_features.json':
            return self.paths.beta_features_file
        if member == 'integrations/mcp_connections.json':
            return self.paths.mcp_connections_file
        if member == "skills/custom_skills.json":
            return self.paths.custom_skills_file
        if member == "workflows/workflows.json":
            return self.paths.workflows_file
        if member == "workflows/workflow_history.json":
            return self.paths.workflow_history_file
        if member == "integrations/integrations.json":
            return self.paths.integrations_file
        if member == "databases/learning.sqlite3":
            return self.paths.learning_database
        if _safe_member_category(member) == "examples":
            return self.paths.verified_images_dir.joinpath(*PurePosixPath(member).parts[2:])
        if _safe_member_category(member) == 'memory':
            return self.paths.memory_images_dir / PurePosixPath(member).name
        raise BackupError("Backup references an unsupported data file.")

    def _category_targets(self, category):
        targets = [getattr(self.paths, name) for name in _CATEGORY_TARGETS[category]]
        if category == "evaluation":
            database = self.paths.learning_database
            targets.extend((Path(str(database) + "-wal"), Path(str(database) + "-shm")))
        return targets

    @staticmethod
    def _remove_target(path):
        path = Path(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def _before_restore_path(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.paths.backup_dir / f"before-restore-{stamp}-{uuid.uuid4().hex[:8]}.zip"

    def restore_backup(self, archive_path):
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="snapflow-restore-", dir=self.paths.cache_dir) as work:
                work_root = Path(work)
                staged_root = work_root / "files"
                inspection = self._stage_and_validate(archive_path, staged_root)
                if not inspection.categories:
                    raise BackupError("This backup contains no application data to restore.")

                current_backup = self.export_backup(self._before_restore_path())
                rollback_root = work_root / "rollback"
                moved = []
                installed = []
                try:
                    for category in inspection.categories:
                        for target in self._category_targets(category):
                            if target.is_symlink():
                                raise BackupError(f"Refusing to replace a symbolic link: {target.name}")
                            if not target.exists():
                                continue
                            rollback_target = rollback_root / category / target.name
                            rollback_target.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(target, rollback_target)
                            moved.append((target, rollback_target))

                    for member in sorted(self._members_in_archive(archive_path)):
                        target = self._target_for_member(member)
                        staged = staged_root.joinpath(*PurePosixPath(member).parts)
                        if member.startswith("examples/verified_images/"):
                            target = self.paths.verified_images_dir
                            staged = staged_root / "examples" / "verified_images"
                            if target in installed:
                                continue
                        if member.startswith('memory/images/'):
                            target = self.paths.memory_images_dir
                            staged = staged_root / 'memory' / 'images'
                            if target in installed:
                                continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged, target)
                        installed.append(target)
                except Exception as exc:
                    rollback_error = None
                    for target in reversed(installed):
                        try:
                            self._remove_target(target)
                        except OSError as rollback_exc:
                            rollback_error = rollback_error or rollback_exc
                    for target, saved in reversed(moved):
                        try:
                            if saved.exists():
                                target.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(saved, target)
                        except OSError as rollback_exc:
                            rollback_error = rollback_error or rollback_exc
                    if rollback_error:
                        raise BackupError(
                            "Restore failed and automatic rollback was incomplete. The pre-restore backup is at "
                            f"{current_backup}. Details: {rollback_error}"
                        ) from exc
                    if isinstance(exc, BackupError):
                        raise
                    raise BackupError(
                        f"Restore failed; the previous files were put back. A backup is at {current_backup}."
                    ) from exc
                return BackupRestoreResult(current_backup, inspection.categories)
        except BackupError:
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
            raise BackupError(f"Unable to restore the backup safely: {exc}") from exc

    @staticmethod
    def _members_in_archive(archive_path):
        with zipfile.ZipFile(archive_path, "r") as archive:
            return [name for name in archive.namelist() if name != "manifest.json"]

    def storage_usage(self):
        if not self.paths.data_dir.exists():
            return 0
        total = 0
        for item in self.paths.data_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                total += item.stat().st_size
            except OSError:
                continue
        return total


def _validate_local_model_registry(data):
    """Validate inert registry metadata without managers or filesystem probes.

    Absolute model paths can describe a different machine after restoration.
    LocalModelManager must still establish ownership before using those paths.
    """
    from src.ai.local_models import LocalModel
    from src.ai.models import ModelCapabilities

    if (not isinstance(data, dict) or set(data) != {"version", "models"} or
            type(data["version"]) is not int or data["version"] != 1 or
            not isinstance(data["models"], list)):
        raise ValueError("Local model registry has an unsupported structure.")
    ids = set()
    capability_names = {'text', 'vision', 'embeddings', 'structured_output', 'tool_calling'}
    for item in data['models']:
        if not isinstance(item, dict) or not isinstance(item.get('capabilities'), dict):
            raise ValueError("Local model records and capabilities must be JSON objects.")
        values = dict(item)
        capabilities = dict(values.pop('capabilities'))
        modalities = capabilities.get('modalities', [])
        if (not isinstance(modalities, list) or any(not isinstance(value, str) or
                not value.strip() or len(value) > 100 for value in modalities)):
            raise ValueError("Local model modalities must be a list of names.")
        capabilities['modalities'] = tuple(modalities)
        values['capabilities'] = ModelCapabilities(**capabilities)
        for name in ('verified_capabilities', 'self_tests'):
            value = values.get(name, [])
            if not isinstance(value, list):
                raise ValueError("Local model evidence must be a JSON list.")
            values[name] = tuple(value)
        record = LocalModel(**values)
        if (not isinstance(record.id, str) or not re.fullmatch(r'[0-9a-f]{32}', record.id) or
                record.id in ids or record.format not in ('gguf', 'onnx')):
            raise ValueError("Local model registry contains an invalid or duplicate identity.")
        ids.add(record.id)
        for name, maximum in (('display_name', 200), ('backend', 100), ('status', 100)):
            value = getattr(record, name)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("Local model names, backend and status must be valid text.")
        for name in ('expected_ram_mb', 'expected_vram_mb', 'size_bytes'):
            value = getattr(record, name)
            if value is None and name != 'size_bytes':
                continue
            if type(value) is not int or value < 0:
                raise ValueError("Local model sizes must be non-negative integers.")
        for name in ('quantization', 'source', 'license_name', 'imported_at'):
            value = getattr(record, name)
            if value is not None and not isinstance(value, str):
                raise ValueError("Optional local model metadata must be text.")
        for name in ('checksum', 'projector_checksum'):
            value = getattr(record, name)
            if value is not None and (not isinstance(value, str) or
                    not re.fullmatch(r'[0-9a-fA-F]{64}', value)):
                raise ValueError("Local model checksums must be SHA-256 digests.")
        model_path = _model_metadata_path(record.model_path, record.id, 'model.' + record.format)
        if record.projector_path is not None:
            projector_path = _model_metadata_path(record.projector_path, record.id, 'projector.gguf')
            if projector_path.parent != model_path.parent:
                raise ValueError("Local model projector path must share its model directory.")
        if (any(not isinstance(name, str) or name not in capability_names or
                not getattr(record.capabilities, name) for name in record.verified_capabilities) or
                len(set(record.verified_capabilities)) != len(record.verified_capabilities)):
            raise ValueError("Local model verified capabilities are invalid.")
        if len(record.self_tests) > 20:
            raise ValueError("Local model self-test evidence exceeds its history limit.")
        for test in record.self_tests:
            if (not isinstance(test, dict) or set(test) != {'capability', 'success',
                    'output_validated', 'latency_ms', 'runtime_id', 'tested_at'} or
                    not isinstance(test['capability'], str) or test['capability'] not in capability_names or
                    type(test['success']) is not bool or type(test['output_validated']) is not bool or
                    type(test['latency_ms']) not in (int, float) or not math.isfinite(test['latency_ms']) or
                    test['latency_ms'] < 0 or not isinstance(test['runtime_id'], str) or
                    len(test['runtime_id']) > 100 or not isinstance(test['tested_at'], str)):
                raise ValueError("Local model self-test evidence is invalid.")
            timestamp = datetime.fromisoformat(test['tested_at'])
            if timestamp.tzinfo is None:
                raise ValueError("Local model self-test timestamp requires a timezone.")


def _model_metadata_path(value, model_id, filename):
    if not isinstance(value, str) or '\0' in value:
        raise ValueError("Local model path must be valid text.")
    path = PureWindowsPath(value) if '\\' in value or PureWindowsPath(value).drive else PurePosixPath(value)
    if not path.is_absolute() or '..' in path.parts or path.name != filename or path.parent.name != model_id:
        raise ValueError("Local model path does not match the model record.")
    return path


def _safe_member_category(member):
    if not isinstance(member, str) or not member or "\\" in member or member.startswith("/"):
        return None
    pure = PurePosixPath(member)
    if (pure.is_absolute() or pure.as_posix() != member or ":" in member or
            any(part in ("", ".", "..") for part in pure.parts)):
        return None
    if member in _FIXED_MEMBERS:
        return _FIXED_MEMBERS[member]
    if (len(pure.parts) == 3 and pure.parts[:2] == ("examples", "verified_images") and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", pure.parts[2]) and
            not pure.parts[2].endswith((".", " ")) and
            pure.parts[2].split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3",
                                                         "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                                                         "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6",
                                                         "LPT7", "LPT8", "LPT9"}):
        return "examples"
    if (len(pure.parts) == 3 and pure.parts[:2] == ('memory', 'images') and
            re.fullmatch(r'[0-9a-f]{32}(?:-thumb)?\.png', pure.parts[2])):
        return 'memory'
    return None
