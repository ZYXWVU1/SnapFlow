"""Qt file lock that prevents duplicate SnapFlow processes."""
from pathlib import Path

from PySide6.QtCore import QLockFile

from src.paths import AppPaths


class SingleInstanceGuard:
    """Acquire a per-user Qt lock before initializing app services."""

    def __init__(self, paths=None, *, lock_path=None):
        self.paths = paths or AppPaths()
        self.lock_path = Path(lock_path) if lock_path is not None else self.paths.data_dir / ".snapflow.lock"
        self._lock = QLockFile(str(self.lock_path))
        self._owns_lock = False

    def acquire(self):
        """Return True for the primary process, False when another owns the lock."""
        if self._owns_lock:
            return True
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            if self._lock.tryLock(0):
                self._owns_lock = True
                return True
        except OSError as exc:
            raise RuntimeError(f"Unable to create the SnapFlow instance lock: {exc}") from exc

        if self._lock.error() == QLockFile.LockError.LockFailedError:
            return False
        raise RuntimeError(f"Unable to acquire the SnapFlow instance lock: {self._lock.error().name}")

    def close(self):
        if self._owns_lock:
            self._lock.unlock()
            self._owns_lock = False
