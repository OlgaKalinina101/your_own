"""Workbench — AI's short-term scratchpad.

Notes are appended to ``data/autonomy/{account_id}/workbench.md`` with
timestamps.  Entries older than WORKBENCH_MAX_AGE_HOURS are considered
stale and will be rotated out (archived to Chroma) at the start of the
next reflection cycle.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger("autonomy.workbench")

WORKBENCH_MAX_AGE_HOURS = 48
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "autonomy"
_lock = Lock()


def _path(account_id: str) -> Path:
    p = _DATA_DIR / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "workbench.md"


def append(account_id: str, text: str) -> None:
    """Append a timestamped note to the workbench."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n---\n[{ts}]\n{text.strip()}\n"
    path = _path(account_id)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    logger.debug("[workbench:%s] appended %d chars", account_id, len(text))


def read(account_id: str) -> str:
    """Return the full workbench contents (may be empty)."""
    path = _path(account_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def search(account_id: str, query: str) -> str:
    """Simple keyword search across workbench notes. Returns matching blocks."""
    content = read(account_id)
    if not content:
        return "(workbench is empty)"
    query_lower = query.lower()
    blocks = content.split("\n---\n")
    matches: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if query_lower in block.lower():
            matches.append(block)
    if not matches:
        return f"No notes matching '{query}'."
    return "\n---\n".join(matches[-10:])


def get_stale_entries(account_id: str) -> list[tuple[str, str]]:
    """Return (timestamp_str, text) tuples for entries older than max age."""
    from datetime import timedelta
    content = read(account_id)
    if not content:
        return []

    stale: list[tuple[str, str]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WORKBENCH_MAX_AGE_HOURS)
    blocks = content.split("\n---\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0].strip("[]")
        try:
            ts = datetime.strptime(first, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            stale.append((first, "\n".join(lines[1:]).strip()))
    return stale


def remove_stale(account_id: str) -> None:
    """Remove entries older than max age from the workbench file."""
    from datetime import timedelta
    content = read(account_id)
    if not content:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=WORKBENCH_MAX_AGE_HOURS)
    blocks = content.split("\n---\n")
    kept: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0].strip("[]")
        try:
            ts = datetime.strptime(first, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            kept.append(block)
            continue
        if ts >= cutoff:
            kept.append(block)

    path = _path(account_id)
    with _lock:
        if kept:
            path.write_text("\n---\n" + "\n---\n".join(kept) + "\n", encoding="utf-8")
        else:
            path.write_text("", encoding="utf-8")
    logger.info("[workbench:%s] removed stale entries, kept %d blocks", account_id, len(kept))
