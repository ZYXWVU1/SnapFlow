"""Local redacted logs, safe error categories, and explicit support bundles."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import sqlite3
import sys
import tempfile
import uuid
import zipfile

from src.app_version import APP_NAME, APP_VERSION
from src.paths import AppPaths


_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b((?:AI_)?API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(https?://)([^:/\s@]+):([^@/\s]+)@"),
)
_MAX_LOG_FILE_BYTES = 512 * 1024
_MAX_BUNDLE_LOG_BYTES = 2 * 1024 * 1024


def redact_text(value, *, home=None):
    """Remove common secret forms and the current user's home path."""
    text = str(value)
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if index == 0:
            text = pattern.sub("Bearer [REDACTED]", text)
        elif index == 1:
            text = pattern.sub("[REDACTED_API_KEY]", text)
        elif index == 2:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        else:
            text = pattern.sub(r"\1[REDACTED]@", text)
    if home is None:
        try:
            home = str(Path.home())
        except OSError:
            home = ""
    if home:
        text = re.sub(re.escape(str(home)), "<USER_HOME>", text, flags=re.IGNORECASE)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return redact_text(super().format(record))

    def formatException(self, exc_info):
        return redact_text(super().formatException(exc_info))


def configure_logging(paths=None):
    """Configure a local rotating log without adding a second handler on relaunch."""
    paths = paths or AppPaths()
    if paths.log_dir.is_symlink():
        raise OSError("The SnapFlow log directory is a symbolic link.")
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.log_dir / "snapflow.log"
    if log_path.is_symlink():
        raise OSError("The SnapFlow log file is a symbolic link.")
    root = logging.getLogger()
    existing = next((item for item in root.handlers if getattr(item, "_snapflow_log", False)), None)
    if existing is None:
        handler = RotatingFileHandler(
            log_path, maxBytes=1024 * 1024,
            backupCount=3, encoding="utf-8")
        handler._snapflow_log = True
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return log_path


@dataclass(frozen=True)
class ErrorReport:
    category: str
    user_message: str
    reference_id: str


@dataclass(frozen=True)
class HealthCheckResult:
    healthy: bool
    data_writable: bool
    logs_writable: bool
    database_status: str


_ERROR_GUIDANCE = {
    "provider": "The AI provider could not complete the request. Check the selected model and provider status.",
    "network": "SnapFlow could not reach the configured service. Check the connection and try again.",
    "authentication": "Your AI provider credentials appear invalid. Check the key and account access in Settings.",
    "rate_limit": "The provider quota or rate limit was reached. Check your account and try again later.",
    "screenshot_capture": "SnapFlow could not capture the screen. Unlock the desktop, try another display, or use Capture again.",
    "hotkey": "SnapFlow could not register this shortcut. Choose another shortcut in Settings; the Capture button is still available.",
    "database": "SnapFlow could not access its local database. Close other instances and check the data folder permissions.",
    "migration": "SnapFlow could not migrate existing data safely. Keep the original files and check the local backup before retrying.",
    "workflow": "The Workflow could not finish. Review its history, mappings, and integration connection before retrying.",
    "integration": "The integration request failed. Check the connection and requested permissions in Settings.",
    "file_permission": "SnapFlow could not access a local file. Check folder permissions and available disk space.",
    "update": "SnapFlow could not check for updates. Check the internet connection and try again later.",
    "configuration": "A setting or input is invalid. Review the value in Settings and try again.",
    "credentials": "Windows Credential Manager could not be accessed. Replace or remove the saved key in Settings.",
    "unexpected": "SnapFlow encountered an unexpected error. Export a support bundle from Settings for review.",
}


def classify_error(error, *, context=None):
    """Map failures to a safe category and user message without echoing exception data."""
    name = type(error).__name__
    message = str(error).lower()
    status = getattr(error, 'status_code', None)
    if status == 401:
        category = 'authentication'
    elif status == 429 or 'rate limit' in message or 'quota' in message:
        category = 'rate_limit'
    elif name == 'CredentialError':
        category = 'credentials'
    elif name in {'MigrationError', 'MigrationConflictError', 'DatabaseMigrationError'}:
        category = 'migration'
    elif isinstance(error, sqlite3.Error) or name in {'DatabaseError', 'BackupError'}:
        category = 'database'
    elif isinstance(error, PermissionError):
        category = 'file_permission'
    elif name in {'APITimeoutError', 'APIConnectionError', 'TimeoutError', 'ConnectionError'} or 'timed out' in message:
        category = 'network'
    elif name in {'AnalysisError', 'APIStatusError'}:
        category = 'provider'
    elif isinstance(error, (OSError,)):
        category = 'file_permission'
    elif isinstance(error, ValueError):
        category = 'configuration'
    else:
        category = 'unexpected'
    if context in _ERROR_GUIDANCE and not (
            category in {'authentication', 'rate_limit', 'credentials', 'database', 'migration', 'file_permission'}
            and category != context):
        category = context
    return ErrorReport(category, _ERROR_GUIDANCE[category], uuid.uuid4().hex[:12])


class DiagnosticsService:
    """Export only runtime metadata and redacted SnapFlow logs, by explicit request."""

    def __init__(self, paths=None):
        self.paths = paths or AppPaths()

    @staticmethod
    def _directory_is_writable(directory):
        directory = Path(directory)
        if directory.is_symlink():
            return False
        probe = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='.snapflow-health-', dir=directory,
                                             delete=False) as temporary:
                probe = Path(temporary.name)
            return True
        except OSError:
            return False
        finally:
            if probe is not None:
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    pass

    def run_basic_health_check(self):
        """Check local data/log write access and the learning DB without exporting content."""
        data_writable = self._directory_is_writable(self.paths.data_dir)
        logs_writable = self._directory_is_writable(self.paths.log_dir)
        database_status = 'not created'
        database = self.paths.learning_database
        if database.is_symlink():
            database_status = 'needs attention'
        elif database.exists():
            try:
                connection = sqlite3.connect(str(database), timeout=1.0)
                try:
                    row = connection.execute('PRAGMA quick_check(1)').fetchone()
                finally:
                    connection.close()
                database_status = 'ok' if row == ('ok',) else 'needs attention'
            except sqlite3.Error:
                database_status = 'needs attention'
        healthy = data_writable and logs_writable and database_status != 'needs attention'
        return HealthCheckResult(healthy, data_writable, logs_writable, database_status)

    @staticmethod
    def _runtime_info():
        try:
            from PySide6 import __version__ as qt_version
        except ImportError:
            qt_version = "unavailable"
        return {
            "application": APP_NAME,
            "app_version": APP_VERSION,
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "qt_version": qt_version,
            "packaged": bool(getattr(sys, "frozen", False)),
        }

    def export_bundle(self, destination):
        destination = Path(destination)
        if destination.suffix.lower() != ".zip":
            raise ValueError("Choose a .zip file for the support bundle.")
        if (destination.exists() and destination.is_dir()) or destination.is_symlink():
            raise ValueError("The support bundle destination must be a regular file path.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        log_payloads = []
        total_bytes = 0
        if self.paths.log_dir.exists() and not self.paths.log_dir.is_symlink():
            for path in sorted(self.paths.log_dir.glob("snapflow.log*")):
                if (path.name != "snapflow.log" and not re.fullmatch(r"snapflow\.log\.\d+", path.name)):
                    continue
                if path.is_symlink() or not path.is_file() or total_bytes >= _MAX_BUNDLE_LOG_BYTES:
                    continue
                with path.open("rb") as stream:
                    payload = stream.read(_MAX_LOG_FILE_BYTES + 1)
                if len(payload) > _MAX_LOG_FILE_BYTES:
                    payload = b"[Earlier log content truncated.]\n" + payload[-_MAX_LOG_FILE_BYTES:]
                payload = redact_text(payload.decode("utf-8", errors="replace")).encode("utf-8")
                remaining = _MAX_BUNDLE_LOG_BYTES - total_bytes
                payload = payload[:remaining]
                if not payload:
                    break
                name = "logs/" + path.name
                log_payloads.append((name, payload))
                entries.append({"path": name, "size": len(payload),
                                "sha256": hashlib.sha256(payload).hexdigest()})
                total_bytes += len(payload)

        info_payload = (json.dumps(self._runtime_info(), indent=2, sort_keys=True) + "\n").encode("utf-8")
        entries.append({"path": "diagnostics.json", "size": len(info_payload),
                        "sha256": hashlib.sha256(info_payload).hexdigest()})
        manifest = {
            "format": "snapflow-support-bundle",
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": entries,
        }
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
                archive.writestr("diagnostics.json", info_payload)
                for name, payload in log_payloads:
                    archive.writestr(name, payload)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination
