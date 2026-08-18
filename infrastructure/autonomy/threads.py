"""Open-threads board — the AI's always-in-view list of unfinished threads.

A fourth memory layer, distinct from the other three:
  - Chroma key_info  — the library: the past, retrieved by query.
  - workbench.md     — the desk: today's thoughts, decays by time (>48h archived).
  - identity.md      — the skin: canon, slow and durable.
  - threads.md (this)— the board: *open threads* that must live forward — counters,
    debts, a topic to revive, a word under examination. Always shown in context,
    just above the workbench. Nothing leaves by time — only by an explicit
    "done" (unpin). That is the whole point: the board is present-continuous.

Stored at ``data/autonomy/{account_id}/threads.md``, one line per thread:

    # Доска открытых нитей
    - [#7fa2 | 2026-08-17 14:30] счёт фотографий по новым правилам: 1
    - [#c3d1 | 2026-08-18 09:00] долг: Кёсем, перенесён на завтра

The ``#id`` is a short stable handle so a specific thread can be closed or
updated from autonomy (indices would shift as threads come and go).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock

logger = logging.getLogger("autonomy.threads")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "autonomy"
_lock = Lock()

_TITLE = "# Доска открытых нитей\n"
_LINE_RE = re.compile(
    r"^- \[#(?P<id>[0-9a-f]{3,}) \| (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*(?P<text>.*)$"
)

# A parsed thread: (id, timestamp_str "YYYY-MM-DD HH:MM", text)
Thread = tuple[str, str, str]


def _path(account_id: str) -> Path:
    p = _DATA_DIR / account_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "threads.md"


def _gen_id(existing: set[str]) -> str:
    """Return a short hex handle not already in *existing*."""
    while True:
        tid = uuid.uuid4().hex[:4]
        if tid not in existing:
            return tid


def read(account_id: str) -> str:
    """Return the full board file contents (may be empty)."""
    path = _path(account_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def parse(content: str) -> list[Thread]:
    """Parse board lines into (id, ts, text) tuples, in file order."""
    threads: list[Thread] = []
    for line in content.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            threads.append((m.group("id"), m.group("ts"), m.group("text").strip()))
    return threads


def list_threads(account_id: str) -> list[Thread]:
    return parse(read(account_id))


def _write(account_id: str, threads: list[Thread]) -> None:
    parts = [_TITLE]
    for tid, ts, text in threads:
        parts.append(f"- [#{tid} | {ts}] {text}")
    body = "\n".join(parts).rstrip() + "\n"
    path = _path(account_id)
    with _lock:
        path.write_text(body, encoding="utf-8")


def pin(account_id: str, text: str) -> str | None:
    """Hang a new open thread. Returns its id.

    De-duplicates on normalised text — if an identical thread already hangs,
    the existing id is returned instead of adding a copy.
    """
    from infrastructure.settings_store import now_local, TIME_FMT

    clean = (text or "").strip()
    if not clean:
        return None

    threads = list_threads(account_id)
    norm = clean.lower()
    for tid, _ts, existing in threads:
        if existing.strip().lower() == norm:
            logger.debug("[threads:%s] pin skipped — duplicate of #%s", account_id, tid)
            return tid

    tid = _gen_id({t[0] for t in threads})
    threads.append((tid, now_local().strftime(TIME_FMT), clean))
    _write(account_id, threads)
    logger.info("[threads:%s] pinned #%s: %s", account_id, tid, clean[:60])
    return tid


def unpin(account_id: str, thread_id: str, *, archive: bool = True) -> bool:
    """Close an open thread ("done"). Removes it from the board.

    By default drops a one-line note on the workbench so the fact that the
    thread was closed survives (and can later rotate into memory).
    """
    tid = (thread_id or "").lstrip("#").strip().lower()
    threads = list_threads(account_id)
    kept: list[Thread] = []
    closed_text: str | None = None
    for t in threads:
        if t[0].lower() == tid:
            closed_text = t[2]
        else:
            kept.append(t)

    if closed_text is None:
        logger.info("[threads:%s] unpin: #%s not found", account_id, tid)
        return False

    _write(account_id, kept)
    logger.info("[threads:%s] unpinned #%s: %s", account_id, tid, closed_text[:60])

    if archive:
        try:
            from infrastructure.autonomy import workbench as wb
            wb.append(account_id, f"Нить закрыта (сделано): {closed_text}")
        except Exception as exc:
            logger.warning("[threads:%s] unpin archive failed: %s", account_id, exc)
    return True


def update(account_id: str, thread_id: str, new_text: str) -> bool:
    """Replace a thread's text, keeping its id and original date (age preserved)."""
    tid = (thread_id or "").lstrip("#").strip().lower()
    clean = (new_text or "").strip()
    if not clean:
        return False

    threads = list_threads(account_id)
    found = False
    updated: list[Thread] = []
    for t in threads:
        if t[0].lower() == tid:
            updated.append((t[0], t[1], clean))
            found = True
        else:
            updated.append(t)

    if not found:
        logger.info("[threads:%s] update: #%s not found", account_id, tid)
        return False

    _write(account_id, updated)
    logger.info("[threads:%s] updated #%s: %s", account_id, tid, clean[:60])
    return True


def _age_label(ts_str: str, lang: str) -> str:
    """Render a thread's touch-date as 'с DD.MM.YYYY' plus its age in days."""
    from infrastructure.settings_store import now_local, get_user_tz, TIME_FMT

    try:
        ts = datetime.strptime(ts_str, TIME_FMT).replace(tzinfo=get_user_tz())
    except ValueError:
        return ""

    date_part = ts.strftime("%d.%m.%Y")
    days = (now_local() - ts).days
    if days <= 0:
        age = "сегодня" if lang == "ru" else "today"
    elif lang == "ru":
        age = f"{days} дн"
    else:
        age = f"{days}d"
    return f"с {date_part} · {age}" if lang == "ru" else f"since {date_part} · {age}"


def render_block(account_id: str, lang: str = "ru", empty_label: str = "") -> str:
    """Render the board as a numbered, dated list for injection into a prompt.

    Returns *empty_label* when the board is empty.
    """
    threads = list_threads(account_id)
    if not threads:
        return empty_label

    lines: list[str] = []
    for i, (tid, ts, text) in enumerate(threads, 1):
        lines.append(f"{i}. {text} — {_age_label(ts, lang)} · #{tid}")
    return "\n".join(lines)
