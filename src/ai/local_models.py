"""Import and manage inert local model data in per-user AppPaths storage.

Weights are never executed or downloaded. Imports copy to staging, verify a
SHA-256 digest, and publish the manifest only after atomic final placement.
These synchronous methods are intended for an existing Qt worker, not startup.
"""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from src.ai.models import ModelCapabilities
from src.paths import AppPaths


_REGISTRY_LOCKS = {}
_REGISTRY_LOADED = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()


class ModelManagementError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalModel:
    id: str
    display_name: str
    backend: str
    format: str
    model_path: str
    capabilities: ModelCapabilities
    quantization: str | None = None
    expected_ram_mb: int | None = None
    expected_vram_mb: int | None = None
    source: str | None = None
    license_name: str | None = None
    checksum: str | None = None
    status: str = "imported"
    projector_path: str | None = None
    projector_checksum: str | None = None
    size_bytes: int = 0
    imported_at: str | None = None
    verified_capabilities: tuple[str, ...] = ()
    self_tests: tuple[dict, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelDeletePreview:
    model_id: str
    display_name: str
    size_bytes: int
    loaded: bool


class LocalModelManager:
    """Manifest capabilities are explicit declarations, not load-time guesses."""

    _FORMATS = {".gguf": "gguf", ".onnx": "onnx"}
    _CAPABILITIES = {"text", "vision", "embeddings", "structured_output", "tool_calling"}
    _CHECKSUM = re.compile(r"[0-9a-fA-F]{64}\Z")
    _ID = re.compile(r"[0-9a-f]{32}\Z")

    def __init__(self, paths=None, *, loaded_model_ids=None):
        self.paths = paths or AppPaths()
        self._loaded_model_ids = loaded_model_ids
        # Dialogs can own separate managers. Serialize their manifest updates
        # within this application process to avoid losing concurrent imports.
        key = os.path.normcase(str(self.paths.model_records_file.resolve()))
        with _REGISTRY_LOCKS_GUARD:
            self._lock = _REGISTRY_LOCKS.setdefault(key, threading.RLock())
            self._loaded = _REGISTRY_LOADED.setdefault(key, set())

    def import_model(self, source_path, *, display_name=None, backend="llama.cpp", capabilities=None,
                     projector_path=None, expected_checksum=None, expected_projector_checksum=None,
                     quantization=None, expected_ram_mb=None, expected_vram_mb=None, source=None,
                     license_name=None, progress=None, cancel=None):
        """Copy one GGUF/ONNX and optional GGUF projector; never overwrite files.

        ``progress(done_bytes, total_bytes)`` and an Event/callable ``cancel``
        allow an existing GUI worker to report progress and cancel safely.
        ONNX imports are standalone files; models with external tensor files
        need a future package importer and are not automatically traversed.
        """
        with self._lock:
            records = self._read_records()
            source_path = self._source_file(source_path)
            model_format = self._FORMATS[source_path.suffix.lower()]
            caps = capabilities or ModelCapabilities()
            if not isinstance(caps, ModelCapabilities):
                raise ModelManagementError("Use an explicit ModelCapabilities manifest.")
            backend = str(backend).strip()
            if not backend or len(backend) > 100:
                raise ModelManagementError("A valid model backend is required.")
            projector = self._source_file(projector_path, projector=True) if projector_path else None
            if caps.vision and backend in ("llama.cpp", "llama-server") and projector is None:
                raise ModelManagementError("A verified matching GGUF projector is required for llama.cpp vision.")
            for expected in (expected_checksum, expected_projector_checksum):
                if expected is not None and not self._CHECKSUM.fullmatch(str(expected)):
                    raise ModelManagementError("Expected checksum must be a SHA-256 digest.")
            for value in (expected_ram_mb, expected_vram_mb):
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                    raise ModelManagementError("Memory estimates must be non-negative MB values.")
            self._check_cancel(cancel)
            base = self.paths.models_dir
            base.mkdir(parents=True, exist_ok=True)
            total = source_path.stat().st_size + (projector.stat().st_size if projector else 0)
            if shutil.disk_usage(base).free < total + 1024 ** 2:
                raise ModelManagementError("Insufficient disk space for model import.")
            model_id = uuid.uuid4().hex
            destination = base / model_id
            staging = base / (".import-" + model_id)
            staging.mkdir()
            copied = []
            placed = False
            try:
                model_filename = "model." + model_format
                checksum = self._copy_checked(source_path, staging / model_filename, expected_checksum,
                                              cancel, progress, 0, total)
                copied.append(model_filename)
                projector_checksum = None
                if projector:
                    projector_checksum = self._copy_checked(projector, staging / "projector.gguf",
                                                            expected_projector_checksum, cancel, progress,
                                                            source_path.stat().st_size, total)
                    copied.append("projector.gguf")
                self._check_cancel(cancel)
                if destination.exists():
                    raise ModelManagementError("Model destination already exists.")
                os.replace(staging, destination)
                placed = True
                record = LocalModel(model_id, str(display_name or source_path.stem).strip()[:200] or "Local model",
                                    backend, model_format, str(destination / model_filename), caps,
                                    quantization=quantization, expected_ram_mb=expected_ram_mb,
                                    expected_vram_mb=expected_vram_mb, source=source, license_name=license_name,
                                    checksum=checksum, projector_path=str(destination / "projector.gguf") if projector else None,
                                    projector_checksum=projector_checksum, size_bytes=total,
                                    imported_at=datetime.now(timezone.utc).isoformat())
                self._write_records([*records, record])
                return record
            except Exception as exc:
                # Only known staging/model filenames are removed, never a tree.
                cleanup_dir = destination if placed else staging
                for filename in ("model." + model_format, "projector.gguf"):
                    candidate = self._safe_owned_path(cleanup_dir / filename, cleanup_dir)
                    if candidate.is_file():
                        candidate.unlink()
                if cleanup_dir.exists():
                    try:
                        cleanup_dir.rmdir()
                    except OSError:
                        pass
                if isinstance(exc, ModelManagementError):
                    raise
                raise ModelManagementError("Model import failed; no model was installed.") from exc

    def list_models(self):
        with self._lock:
            return self._read_records()

    def get_model(self, model_id):
        with self._lock:
            return self._find(self._read_records(), model_id)

    def set_loaded_model(self, model_id, loaded=True):
        with self._lock:
            if loaded:
                self._find(self._read_records(), model_id)
                self._loaded.add(model_id)
            else:
                self._loaded.discard(model_id)

    def delete_preview(self, model_id):
        with self._lock:
            record = self._find(self._read_records(), model_id)
            owned = self._owned_files(record)
            size = sum(path.stat().st_size for path in owned if path.is_file())
            return ModelDeletePreview(record.id, record.display_name, size, self._is_loaded(record.id))

    def delete_model(self, model_id, *, confirmed=False):
        with self._lock:
            if confirmed is not True:
                raise ModelManagementError("Explicit confirmation is required to delete model files.")
            records = self._read_records()
            record = self._find(records, model_id)
            if self._is_loaded(record.id):
                raise ModelManagementError("Unload the model before deleting its files.")
            owned = self._owned_files(record)
            for path in owned:
                if path.exists() and not path.is_file():
                    raise ModelManagementError("Model-owned path is not a regular data file.")
            for path in owned:
                path.unlink(missing_ok=True)
            directory = self.paths.models_dir / record.id
            try:
                directory.rmdir()  # preserve unrecognized files
            except OSError:
                pass
            self._write_records([item for item in records if item.id != record.id])
            self._loaded.discard(record.id)

    def record_self_test(self, model_id, capability, *, success, output_validated, latency_ms, runtime_id):
        """Save content-free test evidence supplied by the runtime validator."""
        if capability not in self._CAPABILITIES:
            raise ModelManagementError("Unsupported capability self-test.")
        if not math.isfinite(float(latency_ms)) or latency_ms < 0:
            raise ModelManagementError("Self-test latency must be finite and non-negative.")
        with self._lock:
            records = self._read_records()
            record = self._find(records, model_id)
            if not getattr(record.capabilities, capability):
                raise ModelManagementError("Capability is not declared in the model manifest.")
            verified = set(record.verified_capabilities)
            if success and output_validated:
                verified.add(capability)
            else:
                verified.discard(capability)
            test = {"capability": capability, "success": bool(success),
                    "output_validated": bool(output_validated), "latency_ms": float(latency_ms),
                    "runtime_id": str(runtime_id)[:100], "tested_at": datetime.now(timezone.utc).isoformat()}
            updated = replace(record, verified_capabilities=tuple(sorted(verified)),
                              self_tests=(*record.self_tests[-19:], test))
            self._write_records([updated if item.id == record.id else item for item in records])
            return updated

    def _is_loaded(self, model_id):
        if model_id in self._loaded:
            return True
        if self._loaded_model_ids is not None:
            try:
                return model_id in self._loaded_model_ids()
            except Exception as exc:
                raise ModelManagementError("Could not confirm that the model is unloaded.") from exc
        return False

    @staticmethod
    def _find(records, model_id):
        for record in records:
            if record.id == model_id:
                return record
        raise ModelManagementError("Local model was not found.")

    @classmethod
    def _source_file(cls, value, *, projector=False):
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise ModelManagementError("Model source must be a non-empty regular file.")
        if path.suffix.lower() not in cls._FORMATS or (projector and path.suffix.lower() != ".gguf"):
            raise ModelManagementError("Only GGUF/ONNX model data and GGUF projectors can be imported.")
        if path.suffix.lower() == ".gguf":
            with path.open("rb") as handle:
                if handle.read(4) != b"GGUF":
                    raise ModelManagementError("Invalid GGUF model header.")
        return path

    @staticmethod
    def _check_cancel(cancel):
        if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
            raise ModelManagementError("Model import cancelled.")

    @classmethod
    def _copy_checked(cls, source, destination, expected_checksum, cancel, progress, offset, total):
        before = source.stat()
        digest = hashlib.sha256()
        written = 0
        with source.open("rb") as reader, destination.open("xb") as writer:
            while True:
                cls._check_cancel(cancel)
                chunk = reader.read(1024 ** 2)
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(offset + written, total)
            writer.flush()
            os.fsync(writer.fileno())
        after = source.stat()
        if before.st_size != written or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise ModelManagementError("Model source changed during import.")
        actual = digest.hexdigest()
        if expected_checksum and actual != expected_checksum.lower():
            raise ModelManagementError("Model checksum verification failed.")
        return actual

    @staticmethod
    def _safe_owned_path(path, directory):
        resolved = path.resolve()
        try:
            resolved.relative_to(directory.resolve())
        except ValueError as exc:
            raise ModelManagementError("Model-owned path escaped its storage directory.") from exc
        if path.is_symlink():
            raise ModelManagementError("Model-owned files must not be symlinks.")
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0
        # Windows junctions are reparse points but are not reported as
        # symlinks by Python 3.11. Never follow them during deletion.
        if attributes & 0x400:
            raise ModelManagementError("Model-owned paths must not be Windows reparse points.")
        return path

    def _owned_files(self, record):
        if not self._ID.fullmatch(record.id) or record.format not in ("gguf", "onnx"):
            raise ModelManagementError("Invalid model-owned storage record.")
        directory = self.paths.models_dir / record.id
        self._safe_owned_path(directory, self.paths.models_dir)
        model_path = directory / ("model." + record.format)
        if Path(record.model_path).absolute() != model_path.absolute():
            raise ModelManagementError("Model path is not an owned model file.")
        files = [self._safe_owned_path(model_path, directory)]
        if record.projector_path:
            projector_path = directory / "projector.gguf"
            if Path(record.projector_path).absolute() != projector_path.absolute():
                raise ModelManagementError("Projector path is not an owned model file.")
            files.append(self._safe_owned_path(projector_path, directory))
        return files

    def _read_records(self):
        manifest = self.paths.model_records_file
        if not manifest.exists():
            return []
        try:
            if manifest.stat().st_size > 4 * 1024 ** 2:
                raise ModelManagementError("Local model registry exceeds its size limit.")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("version") != 1
                    or not isinstance(payload.get("models"), list)):
                raise ModelManagementError("Local model registry requires compatibility review.")
            records = []
            ids = set()
            for data in payload["models"]:
                data = dict(data)
                capabilities = dict(data.pop("capabilities"))
                capabilities["modalities"] = tuple(capabilities.get("modalities", ()))
                data["capabilities"] = ModelCapabilities(**capabilities)
                data["verified_capabilities"] = tuple(data.get("verified_capabilities", ()))
                data["self_tests"] = tuple(data.get("self_tests", ()))
                record = LocalModel(**data)
                self._owned_files(record)
                if record.id in ids:
                    raise ModelManagementError("Duplicate model-owned registry record.")
                ids.add(record.id)
                if not Path(record.model_path).is_file():
                    record = replace(record, status="missing")
                records.append(record)
            return records
        except ModelManagementError:
            raise
        except (OSError, TypeError, ValueError, KeyError) as exc:
            raise ModelManagementError("Local model registry is invalid; files were preserved.") from exc

    def _write_records(self, records):
        manifest = self.paths.model_records_file
        manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest.with_name("." + manifest.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump({"version": 1, "models": [asdict(record) for record in records]}, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest)
        finally:
            temporary.unlink(missing_ok=True)
