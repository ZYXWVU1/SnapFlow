"""Explicit restore while preserving a raw damaged database and its sidecars."""
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import stat
import uuid
import zipfile

from src.backup import BackupError, BackupService
from src.observability.store import _check_regular_path


def export_damaged_database(source, destination):
    """Copy raw recovery files without replacing any destination or sidecar."""
    source, destination = Path(source), Path(destination)
    copies = []
    for suffix in ('', '-wal', '-shm'):
        original = source.with_name(source.name + suffix)
        target = destination.with_name(destination.name + suffix)
        _check_regular_path(original)
        _check_regular_path(target)
        if target.exists() or original.resolve() == target.resolve():
            raise FileExistsError('Choose unused export filenames, including database sidecars.')
        if original.exists():
            copies.append((original, target))
        elif not suffix:
            raise FileNotFoundError('The damaged database is unavailable.')
    created = []
    try:
        with ExitStack() as stack:
            streams = []
            # Open every source before creating exports. Exclusive destination
            # creation also refuses a collision appearing after preflight.
            for original, target in copies:
                _check_regular_path(original)
                stream = stack.enter_context(original.open('rb'))
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise OSError('Recovery source must be a regular file.')
                streams.append((stream, target))
            outputs = []
            for stream, target in streams:
                _check_regular_path(target)
                output = stack.enter_context(target.open('xb'))
                created.append((target, os.fstat(output.fileno())))
                outputs.append((stream, output))
            for stream, output in outputs:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
        return destination
    except Exception:
        # At most three files; never remove a file substituted by another
        # writer or follow a newly introduced reparse point during cleanup.
        for target, identity in created:
            try:
                _check_regular_path(target)
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    target.unlink()
            except OSError:
                pass
        raise


def restore_after_corruption(paths, archive):
    service = BackupService(paths)
    inspection = service.inspect_backup(archive)
    if 'evaluation' not in inspection.categories:
        raise BackupError('Database recovery requires a backup containing the learning database.')
    with zipfile.ZipFile(archive) as source:
        if 'databases/learning.sqlite3' not in source.namelist():
            raise BackupError('Database recovery requires the archived learning database.')
    _check_regular_path(paths.learning_database)
    quarantine_dir = paths.backup_dir / ('damaged-database-' + uuid.uuid4().hex)
    _check_regular_path(paths.backup_dir / 'probe')
    quarantine_dir.mkdir(parents=True)
    moved = []
    try:
        for suffix in ('', '-wal', '-shm'):
            original = paths.learning_database.with_name(paths.learning_database.name + suffix)
            _check_regular_path(original)
            if original.exists():
                destination = quarantine_dir / original.name
                os.replace(original, destination)
                moved.append((original, destination))
        service.restore_backup(archive)
    except Exception:
        for original, destination in moved:
            if original.exists():
                os.replace(original, quarantine_dir / ('failed-restore-' + original.name))
            if destination.exists():
                os.replace(destination, original)
        raise
    return quarantine_dir / paths.learning_database.name
