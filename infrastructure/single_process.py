"""A lock that makes the "exactly one process" assumption enforceable.

The state under ``data/`` is read-modify-write with no cross-process locking:
``threads.md``, ``identity.md``, ``vitals.json`` and the workbench rewrite are
all read-then-replace. Within one event loop those calls are synchronous and
therefore serialise; across two processes they do not. Measured with two
processes pinning 40 threads each: **40 of 80 survived**, the file valid, the
losses silent.

That is fine — one process is a reasonable design for a personal AI. What was
not fine is that nothing said so and nothing checked. ``uvicorn --workers 2``,
a systemd unit that restarts before the old process is gone, or a second
terminal are all one keystroke away.

So: acquire on startup, refuse to start if someone else holds it.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from infrastructure.paths import DATA_DIR

logger = logging.getLogger("single_process")

LOCK_PATH = DATA_DIR / ".backend.lock"


class AlreadyRunning(RuntimeError):
    """Another backend process holds the lock."""


class SingleProcessLock:
    """Best-effort exclusive lock on the data directory.

    Uses ``msvcrt`` on Windows and ``fcntl`` elsewhere. Both release the lock
    when the process dies for any reason, including a kill -9, so a crashed
    backend does not leave a lock nobody can clear.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else LOCK_PATH
        self._handle = None

    # The pid is written into a fixed-width field so the locked byte is always
    # byte 0 and always present. Truncating the file would remove the very byte
    # the lock is held on.
    _PID_FIELD = 20

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(" " * self._PID_FIELD, encoding="utf-8")

        handle = open(self.path, "r+", encoding="utf-8")
        try:
            self._lock(handle)
        except OSError as exc:
            holder = self._read_holder()
            handle.close()
            raise AlreadyRunning(
                f"another backend already holds {self.path} (pid {holder}). "
                "The state files under data/ are not safe for two processes: "
                "run one, or point the second at its own data directory."
            ) from exc

        handle.seek(0)
        handle.write(f"{os.getpid():<{self._PID_FIELD}}")
        handle.flush()
        self._handle = handle
        logger.info("[single_process] holding %s (pid %d)", self.path, os.getpid())

    def _read_holder(self) -> str:
        """Who has it, for the error message. An exclusive lock also denies
        reads on Windows, so this is best-effort by design."""
        try:
            return self.path.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._unlock(self._handle)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

    # -- platform bits ---------------------------------------------------

    @staticmethod
    def _lock(handle) -> None:
        if sys.platform == "win32":
            import msvcrt

            # msvcrt locks a byte range starting at the *current position*, so
            # the seek is load-bearing. Without it the position was wherever
            # the previous write left it, two processes locked two different
            # bytes, and the second one sailed past the check — it only failed
            # later, on the write, with a PermissionError nobody was expecting.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle) -> None:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
