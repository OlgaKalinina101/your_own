"""Exactly one backend, enforced rather than assumed.

Run:
    python -m pytest tests/test_single_process.py -v

The state under ``data/`` is read-modify-write with no cross-process locking.
Measured with two processes pinning 40 threads each: **40 of 80 survived**, the
file still valid, the losses silent. Atomic writes did not change that number
and were never going to — they make each *write* whole, not the read-then-write
cycle. The invariant is "one process", and it has to be held at the process
boundary.

These tests run a real second interpreter. The first version of this lock passed
every in-process check and still let a second process through: ``msvcrt.locking``
locks a byte range starting at the *current file position*, and the position was
wherever the previous write had left it, so the two processes locked two
different bytes. Nothing but a second process would have shown that.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from infrastructure.single_process import AlreadyRunning, SingleProcessLock

REPO = Path(__file__).resolve().parents[1]


def _try_acquire_in_another_process(lock_path: Path) -> str:
    """Returns "acquired" or "refused"."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        from infrastructure.single_process import SingleProcessLock, AlreadyRunning
        try:
            SingleProcessLock({str(lock_path)!r}).acquire()
            print("acquired")
        except AlreadyRunning:
            print("refused")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class TestTheSecondProcessIsStopped:
    def test_a_second_backend_is_refused(self, tmp_path):
        lock = SingleProcessLock(tmp_path / ".backend.lock")
        lock.acquire()
        try:
            assert _try_acquire_in_another_process(lock.path) == "refused"
        finally:
            lock.release()

    def test_the_lock_is_free_once_the_first_one_lets_go(self, tmp_path):
        lock = SingleProcessLock(tmp_path / ".backend.lock")
        lock.acquire()
        lock.release()

        assert _try_acquire_in_another_process(lock.path) == "acquired"

    def test_the_refusal_says_what_to_do(self, tmp_path):
        first = SingleProcessLock(tmp_path / ".backend.lock")
        first.acquire()
        try:
            with pytest.raises(AlreadyRunning) as caught:
                SingleProcessLock(tmp_path / ".backend.lock").acquire()
        finally:
            first.release()

        message = str(caught.value)
        assert "not safe for two processes" in message
        assert "own data directory" in message

    def test_the_lock_survives_the_pid_being_written(self, tmp_path):
        """The regression: the pid write moved the file position.

        The first version locked a byte at the position left by that write, so
        the second process locked a different byte and was let through.
        """
        lock = SingleProcessLock(tmp_path / ".backend.lock")
        lock.acquire()
        try:
            # Not read back while held: on Windows an exclusive lock denies
            # reads too, which is why the refusal message says "pid unknown".
            assert _try_acquire_in_another_process(lock.path) == "refused"
        finally:
            lock.release()
        assert lock.path.read_text(encoding="utf-8").strip(), "no pid was written"

    def test_a_missing_directory_is_created(self, tmp_path):
        lock = SingleProcessLock(tmp_path / "deep" / "deeper" / ".backend.lock")
        lock.acquire()
        try:
            assert lock.path.exists()
        finally:
            lock.release()

    def test_releasing_twice_is_harmless(self, tmp_path):
        lock = SingleProcessLock(tmp_path / ".backend.lock")
        lock.acquire()
        lock.release()
        lock.release()


class TestWhatTheLockIsProtecting:
    """Why the lock exists at all, stated as the measurement that produced it."""

    def test_two_writers_to_one_board_lose_half_the_threads(self, tmp_path):
        """In-process this is serialised; across processes it is not.

        Simulated here rather than forked, because the point is the shape of the
        bug — read, modify, write, with another writer in between — and that
        shape is what the lock exists to prevent from ever running twice.
        """
        import infrastructure.autonomy.threads as threads

        threads._DATA_DIR = tmp_path

        first = threads.list_threads("default")
        threads.pin("default", "нить A")
        # Another process still holds the list it read a moment ago…
        threads._write("default", first + [("bbbb", "2026-08-29 10:00", "нить B")])

        surviving = [text for _id, _ts, text in threads.list_threads("default")]
        assert surviving == ["нить B"], "the lost-update this lock prevents"
