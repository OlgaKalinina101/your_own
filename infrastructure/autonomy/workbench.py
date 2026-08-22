"""Workbench — AI's short-term scratchpad.

Notes are appended to ``data/autonomy/{account_id}/workbench.md`` with
timestamps.  Entries older than WORKBENCH_MAX_AGE_HOURS are considered
stale and will be rotated out (archived to Chroma) at the start of the
next reflection cycle.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger("autonomy.workbench")

WORKBENCH_MAX_AGE_HOURS = 48
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "autonomy"
_lock = Lock()

_TITLE = "# Рабочий стол\n"

_OLD_HDR = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*(?:\(.*\))?\s*$")
_NEW_HDR = re.compile(r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*(?:UTC)?\]\s*$")

_CMD_NAMES = (
    "SEARCH_FACTS", "SEARCH_MEMORIES", "SEARCH_NOTES", "SEARCH_DIALOGUE", "WEB_SEARCH",
    "WRITE_NOTE", "WRITE_IDENTITY", "SEND_MESSAGE", "SCHEDULE_MESSAGE",
    "CANCEL_MESSAGE", "RESCHEDULE_MESSAGE", "REWRITE_MESSAGE",
    "EXTEND", "SLEEP", "RECALL", "WRITE", "HISTORY",
    "SAVED_FACT", "GENERATED_IMAGE",
)
_LEAKED_CMD_RE = re.compile(
    r"^\[(?:" + "|".join(_CMD_NAMES) + r")[\s:|\]]",
    re.IGNORECASE,
)
_UNCLOSED_CMD_RE = re.compile(
    r"\[(?:" + "|".join(_CMD_NAMES) + r"):[^\]]{0,500}$",
    re.IGNORECASE,
)


def _sanitize_note(text: str) -> str:
    """Remove leaked/truncated LLM commands that should not pollute the workbench."""
    lines = text.splitlines()
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _LEAKED_CMD_RE.match(stripped):
            continue
        clean.append(line)
    result = "\n".join(clean)
    result = _UNCLOSED_CMD_RE.sub("", result)
    return result.strip()


def _path(account_id: str) -> Path:
    p = _DATA_DIR / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "workbench.md"


def parse_entries(content: str) -> list[tuple[str, str]]:
    """Parse workbench entries supporting both ``### ts`` and ``---/[ts UTC]`` formats.

    Returns list of ``(timestamp_str, body_text)`` in file order.
    ``timestamp_str`` is always ``YYYY-MM-DD HH:MM`` (no UTC suffix).
    """
    if not content.strip():
        return []

    entries: list[tuple[str, str]] = []
    lines = content.splitlines()
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        ts: str | None = None

        m = _OLD_HDR.match(stripped)
        if m:
            ts = m.group(1)
            i += 1
        elif stripped == "---" and i + 1 < len(lines):
            m = _NEW_HDR.match(lines[i + 1].strip())
            if m:
                ts = m.group(1)
                i += 2
            else:
                i += 1
                continue
        else:
            i += 1
            continue

        body_lines: list[str] = []
        while i < len(lines):
            peek = lines[i].strip()
            if _OLD_HDR.match(peek):
                break
            if peek == "---" and i + 1 < len(lines) and _NEW_HDR.match(lines[i + 1].strip()):
                break
            body_lines.append(lines[i])
            i += 1

        body = "\n".join(body_lines).strip()
        if ts and body:
            entries.append((ts, body))

    return entries


def append(account_id: str, text: str) -> None:
    """Append a timestamped note to the workbench."""
    from infrastructure.settings_store import now_local_str
    clean = _sanitize_note(text)
    if not clean:
        logger.debug("[workbench:%s] sanitized note is empty, skipping", account_id)
        return
    ts = now_local_str()
    path = _path(account_id)
    with _lock:
        if not path.exists() or path.stat().st_size == 0:
            path.write_text(_TITLE, encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n### {ts}\n{clean}\n")
    logger.debug("[workbench:%s] appended %d chars", account_id, len(clean))


def read(account_id: str) -> str:
    """Return the full workbench contents (may be empty)."""
    path = _path(account_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def get_recent_entries(account_id: str, max_entries: int = 3, empty_label: str = "") -> str:
    """Return the last *max_entries* workbench entries wrapped in XML tags.

    Returns *empty_label* (default ``""``) when there are no entries.
    """
    content = read(account_id)
    if not content:
        return empty_label
    entries = parse_entries(content)
    if not entries:
        return empty_label
    parts = [f'<entry ts="{ts}">\n{body}\n</entry>' for ts, body in entries[-max_entries:]]
    return "\n".join(parts)


def search(account_id: str, query: str) -> str:
    """Simple keyword search across workbench notes. Returns matching blocks."""
    content = read(account_id)
    if not content:
        return "(workbench is empty)"
    entries = parse_entries(content)
    if not entries:
        return "(workbench is empty)"
    query_lower = query.lower()
    matches = [
        f"### {ts}\n{body}"
        for ts, body in entries
        if query_lower in body.lower()
    ]
    if not matches:
        return f"No notes matching '{query}'."
    return "\n\n".join(matches[-10:])


def get_stale_entries(account_id: str) -> list[tuple[str, str]]:
    """Return (timestamp_str, text) tuples for entries older than max age."""
    content = read(account_id)
    if not content:
        return []

    from infrastructure.settings_store import now_local, get_user_tz, TIME_FMT
    cutoff = now_local() - timedelta(hours=WORKBENCH_MAX_AGE_HOURS)
    tz = get_user_tz()
    stale: list[tuple[str, str]] = []

    for ts_str, body in parse_entries(content):
        try:
            ts = datetime.strptime(ts_str, TIME_FMT).replace(tzinfo=tz)
        except ValueError:
            continue
        if ts < cutoff:
            stale.append((ts_str, body))

    return stale


def remove_stale(account_id: str) -> None:
    """Remove entries older than max age from the workbench file."""
    content = read(account_id)
    if not content:
        return

    from infrastructure.settings_store import now_local, get_user_tz, TIME_FMT
    cutoff = now_local() - timedelta(hours=WORKBENCH_MAX_AGE_HOURS)
    tz = get_user_tz()
    entries = parse_entries(content)

    kept: list[tuple[str, str]] = []
    for ts_str, body in entries:
        try:
            ts = datetime.strptime(ts_str, TIME_FMT).replace(tzinfo=tz)
        except ValueError:
            kept.append((ts_str, body))
            continue
        if ts >= cutoff:
            kept.append((ts_str, body))

    path = _path(account_id)
    with _lock:
        if kept:
            parts = [_TITLE]
            for ts_str, body in kept:
                parts.append(f"\n\n### {ts_str}\n{body}\n")
            path.write_text("".join(parts), encoding="utf-8")
        else:
            path.write_text(_TITLE, encoding="utf-8")
    logger.info("[workbench:%s] removed stale entries, kept %d blocks", account_id, len(kept))
