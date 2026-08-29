"""The clock. One timezone, and one place to ask for it.

Two rules, and they are the whole module:

* Anything **stored or compared** is an instant, in UTC. Durations, cooldowns,
  "has an hour passed" — all of it happens in UTC and nowhere else.
* Anything **shown** to a human or to the model is in the user's timezone, and
  that timezone is one setting: ``user_timezone`` in ``data/settings.json``.

Both rules were being applied by hand. ``get_user_tz()`` was imported in eleven
files and ``.astimezone(user_tz)`` written out at nine call sites — which works
until one of them is forgotten. One was: the push validator formatted her last
message time straight off the database row, and rows come back UTC-aware. Next
to a local "now" in the same prompt, that read as four hours of silence where
there had been four minutes, and the validator judged whether to interrupt her
on that.

There were also two functions named ``now_local``: this one, tz-aware in the
user's timezone, and one in ``live_store`` that returned naive **system** time —
with an alias beside it literally named ``now_utc`` pointing at the naive one.
They agreed on the machine this was written on, whose system clock also happened
to be UTC+4, so nothing looked wrong. On a server set to UTC they would not have.

The system timezone is never consulted. If it ever appears in an answer here,
that is a bug.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("clock")

TIME_FMT = "%Y-%m-%d %H:%M"

DEFAULT_TIMEZONE = "Asia/Yerevan"

_UTC = timezone.utc


def timezone_name() -> str:
    """The configured IANA name. The single source; everything else derives."""
    from infrastructure.settings_store import load_settings

    return str(load_settings().get("user_timezone") or DEFAULT_TIMEZONE)


def user_tz() -> ZoneInfo:
    """The user's timezone. Falls back to UTC — loudly — if it cannot be loaded."""
    name = timezone_name()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        logger.error(
            "[clock] user_timezone %r is not a timezone (%s) — falling back to UTC. "
            "Every time shown to him will be wrong until this is fixed.",
            name, exc,
        )
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    """The current instant. What durations and comparisons are made of."""
    return datetime.now(_UTC)


def now_local() -> datetime:
    """The current instant, expressed in the user's timezone."""
    return datetime.now(user_tz())


def to_user(dt: datetime) -> datetime:
    """Move *dt* into the user's timezone.

    A naive datetime is taken to be UTC, because that is what a naive value out
    of this system means: the database returns aware UTC, and anything that
    lost its tzinfo on the way lost it after being UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(user_tz())


def format_local(dt: datetime | None, fmt: str = TIME_FMT, *, empty: str = "—") -> str:
    """Render a stored timestamp the way he should read it.

    The only correct way to turn a row's ``created_at`` into text. Calling
    ``strftime`` on the row directly prints UTC, which is the bug this module
    exists for.
    """
    if dt is None:
        return empty
    return to_user(dt).strftime(fmt)


def local_to_utc(naive_dt: datetime) -> datetime:
    """Read a wall-clock time he wrote as local, and return the instant.

    Used for ``[SCHEDULE_MESSAGE: 2026-08-30 09:00]`` — he writes local times
    because we show him local times.
    """
    return naive_dt.replace(tzinfo=user_tz()).astimezone(_UTC)


def now_local_str() -> str:
    """Current local time as ``YYYY-MM-DD HH:MM (Zone)``."""
    return f"{now_local().strftime(TIME_FMT)} ({timezone_name()})"


def label() -> str:
    """Human-readable, e.g. ``Asia/Yerevan, UTC+4``."""
    tz = user_tz()
    offset = datetime.now(tz).utcoffset()
    total = int(offset.total_seconds()) if offset else 0
    sign = "+" if total >= 0 else "-"
    hours, rest = divmod(abs(total), 3600)
    minutes = rest // 60
    utc_part = f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours}:{minutes:02d}"
    return f"{tz}, {utc_part}"
