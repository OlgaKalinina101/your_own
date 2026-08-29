"""Writing and reading the files under ``data/`` without losing them.

Every file there is the only copy of what it holds. There is no transaction
spanning Postgres, Chroma and this directory, and nothing replays a truncated
workbench — so the two failure modes below have to be handled here, once,
rather than at each of the fifteen call sites.

**Half-written files.** ``path.write_text(...)`` truncates first and writes
after. Between those two the old content is gone and the new one is not there
yet. It is a small window, but a power cut is wide enough for it, and the file
it leaves behind parses as garbage. :func:`atomic_write_text` writes a
neighbouring temp file and renames it over the target, so a reader sees either
the whole old file or the whole new one.

**Overwriting the evidence.** When a file does not parse, every reader here
falls back to an empty value — deliberately, because a corrupt panel must not
take the reflection down with it. The damage is done by what happens next: the
first write replaces the unreadable file, and whatever was recoverable in it is
gone for good. :func:`quarantine` moves it aside first, so a bad parse costs
the current read and not the history.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger("state_file")


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace the contents of *path* in one step, or leave them untouched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same directory, so the rename stays on one filesystem and the temp file
    # is visible next to its target if we ever crash between the two steps.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def quarantine(path: str | Path, *, log: logging.Logger | None = None) -> Path | None:
    """Move an unreadable *path* aside. Returns where it went, or None.

    Called before falling back to a default, never instead of it: the caller
    still degrades, it just stops destroying what it could not read.
    """
    path = Path(path)
    log = log or _log
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, dest)
    except OSError as exc:
        log.error("[state] %s is unreadable and could not be set aside: %s", path.name, exc)
        return None
    log.error(
        "[state] %s did not parse; kept the damaged copy as %s and starting from empty",
        path.name, dest.name,
    )
    return dest


def read_json(path: str | Path, *, default: Any = None, log: logging.Logger | None = None) -> Any:
    """Load JSON from *path*, quarantining it if it does not parse.

    A missing file is not damage — it returns *default* quietly. A file that
    exists but is not JSON is damage, and says so at ERROR.
    """
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        (log or _log).error("[state] %s: %s", path.name, exc)
        quarantine(path, log=log)
        return {} if default is None else default
