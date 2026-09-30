"""Versioned migration and consistent backup for the local learning database."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


CURRENT_SCHEMA_VERSION = 4


class DatabaseMigrationError(RuntimeError):
    """Raised when a local database cannot be upgraded safely."""


@dataclass(frozen=True)
class DatabaseMigrationResult:
    schema_version: int
    backup_path: Path | None


def backup_sqlite_database(source, destination):
    """Create an integrity-checked SQLite snapshot, including committed WAL data."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = destination_db = None
    try:
        source_db = sqlite3.connect(source)
        destination_db = sqlite3.connect(destination)
        source_db.backup(destination_db)
        integrity = destination_db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise DatabaseMigrationError(f"SQLite backup integrity check failed for {source.name}.")
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseMigrationError(f"Unable to create a consistent backup of {source.name}: {exc}") from exc
    finally:
        if destination_db is not None:
            destination_db.close()
        if source_db is not None:
            source_db.close()
    return destination


def _check_integrity(connection, path):
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseMigrationError(f"Unable to validate {path.name}: {exc}") from exc
    if not result or result[0] != "ok":
        raise DatabaseMigrationError(f"Database integrity check failed for {path.name}.")


def _migrate_legacy_schema(connection):
    """Record the pre-8B schema as version 1 without rewriting user tables."""
    connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL
    )""")
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
        (1, datetime.now(timezone.utc).isoformat(), "Adopt existing Phase 1–7 learning database schema."),
    )


_MIGRATIONS = {0: _migrate_legacy_schema}


def _migrate_api_usage(connection):
    """Store provider token counts and estimates without request content."""
    connection.execute("""CREATE TABLE IF NOT EXISTS api_usage (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        operation TEXT NOT NULL,
        input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
        cached_input_tokens INTEGER NOT NULL CHECK (cached_input_tokens >= 0),
        estimated_cost_usd REAL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_evaluation ON api_usage(operation, provider, model, timestamp)")
    connection.execute("""CREATE TABLE IF NOT EXISTS api_usage_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
        (2, datetime.now(timezone.utc).isoformat(), "Add token-only API usage and estimate ledger."),
    )


_MIGRATIONS[1] = _migrate_api_usage


def _migrate_api_usage_details(connection):
    """Add privacy-safe timing and request outcome metadata."""
    connection.execute("ALTER TABLE api_usage ADD COLUMN latency_ms INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE api_usage ADD COLUMN succeeded INTEGER NOT NULL DEFAULT 1")
    connection.execute("ALTER TABLE api_usage ADD COLUMN usage_available INTEGER NOT NULL DEFAULT 1")
    connection.execute("ALTER TABLE api_usage ADD COLUMN failure_category TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_success_time ON api_usage(succeeded, timestamp)")
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
        (3, datetime.now(timezone.utc).isoformat(), "Add request latency and outcome summaries."),
    )


_MIGRATIONS[2] = _migrate_api_usage_details


def _migrate_visual_memory(connection):
    """Create opt-in records without joining Phase 7 example data."""
    connection.execute("""CREATE TABLE memory_records (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        source_type TEXT NOT NULL,
        skill_id TEXT,
        skill_version_id TEXT,
        structured_data_json TEXT NOT NULL,
        searchable_text TEXT NOT NULL,
        screenshot_path TEXT,
        thumbnail_path TEXT,
        source_created_at TEXT,
        saved_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        content_hash TEXT,
        schema_version INTEGER NOT NULL
    )""")
    connection.execute("CREATE INDEX idx_memory_saved_at ON memory_records(saved_at)")
    connection.execute("CREATE INDEX idx_memory_content_hash ON memory_records(content_hash)")
    connection.execute("""CREATE TABLE memory_tags (
        memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        PRIMARY KEY(memory_id, tag)
    )""")
    connection.execute("CREATE INDEX idx_memory_tag ON memory_tags(tag)")
    connection.execute("""CREATE TABLE memory_revisions (
        id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
        prior_record_json TEXT NOT NULL,
        changed_at TEXT NOT NULL
    )""")
    connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
        (4, datetime.now(timezone.utc).isoformat(), "Add explicit Visual Memory records and revisions."))


_MIGRATIONS[3] = _migrate_visual_memory


def initialize_learning_database(path, backup_dir=None):
    """Apply ordered schema migrations, backing up the database before upgrades."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    existed = path.exists()
    connection = None
    try:
        connection = sqlite3.connect(path)
        try:
            version_row = connection.execute("PRAGMA user_version").fetchone()
            version = int(version_row[0]) if version_row else 0
        except (TypeError, ValueError, sqlite3.Error) as exc:
            raise DatabaseMigrationError(f"Unable to read the database schema version: {exc}") from exc
        if version < 0 or version > CURRENT_SCHEMA_VERSION:
            raise DatabaseMigrationError(
                f"Database schema version {version} is not supported by this SnapFlow version."
            )
        if version == CURRENT_SCHEMA_VERSION:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if row is None:
                raise DatabaseMigrationError("Database schema marker is missing; data was left untouched.")
            return DatabaseMigrationResult(version, None)

        _check_integrity(connection, path)
        if existed:
            directory = Path(backup_dir) if backup_dir is not None else path.parent / "backups"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = directory / f"learning-schema-v{version}-before-v{CURRENT_SCHEMA_VERSION}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
            backup_sqlite_database(path, backup_path)

        try:
            with connection:
                while version < CURRENT_SCHEMA_VERSION:
                    migration = _MIGRATIONS.get(version)
                    if migration is None:
                        raise DatabaseMigrationError(f"No migration is defined from schema version {version}.")
                    migration(connection)
                    version += 1
                    connection.execute(f"PRAGMA user_version={version}")
            _check_integrity(connection, path)
        except sqlite3.Error as exc:
            raise DatabaseMigrationError(f"Unable to migrate the learning database: {exc}") from exc
        return DatabaseMigrationResult(version, backup_path)
    except sqlite3.Error as exc:
        raise DatabaseMigrationError(f"Unable to open the learning database: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
