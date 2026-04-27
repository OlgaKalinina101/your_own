"""File-based settings store.

Settings live in ``data/settings.json``, soul prompt in ``data/soul.md``.
Both are read/written by the REST API and consumed by the chat endpoint
so that clients never need to send secrets with every request.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SETTINGS_FILE = _DATA_DIR / "settings.json"
_SOUL_FILE = _DATA_DIR / "soul.md"

DEFAULT_MODEL = "anthropic/claude-opus-4.6"

TIME_FMT = "%Y-%m-%d %H:%M"

_DEFAULTS: dict[str, object] = {
    "openrouter_api_key": "",
    "model": DEFAULT_MODEL,
    "temperature": 0.7,
    "top_p": 0.9,
    "history_pairs": 6,
    "memory_cutoff_days": 2,
    # AI identity
    "ai_name": "",
    # Pushy push notifications
    "pushy_api_key": "",
    "pushy_device_token": "",
    # Reflection timing (hours)
    "reflection_cooldown_hours": 4,
    "reflection_interval_hours": 12,
    # User's local timezone (IANA name, e.g. "Asia/Yerevan", "Europe/Moscow")
    "user_timezone": "Asia/Yerevan",
    # Skills that are active in the chat pipeline (None = all enabled)
    "enabled_skills": None,
    # Model used for body image generation (image-to-image with anchor reference)
    "body_image_model": "sourceful/riverflow-v2-fast",
}

_lock = Lock()


def get_user_tz() -> ZoneInfo:
    """Return the configured user timezone, falling back to UTC on error."""
    tz_name = load_settings().get("user_timezone", "Asia/Yerevan")
    try:
        return ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("UTC")


def now_local() -> datetime:
    """Return current time in the user's local timezone."""
    return datetime.now(get_user_tz())


def tz_label() -> str:
    """Return a human-readable timezone label, e.g. ``Asia/Yerevan, UTC+4``."""
    tz = get_user_tz()
    offset = datetime.now(tz).utcoffset()
    total_seconds = int(offset.total_seconds()) if offset else 0
    sign = "+" if total_seconds >= 0 else "-"
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes = remainder // 60
    utc_part = f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours}:{minutes:02d}"
    return f"{tz}, {utc_part}"


def now_local_str() -> str:
    """Return current local time formatted as ``YYYY-MM-DD HH:MM (TZ)``."""
    dt = now_local()
    tz_name = str(get_user_tz())
    return f"{dt.strftime(TIME_FMT)} ({tz_name})"


def local_to_utc(naive_dt: datetime) -> datetime:
    """Interpret a naive datetime as user-local time and convert to UTC.

    Used when parsing SCHEDULE_MESSAGE timestamps that the model writes
    in local time (because we show it local time in the prompt).
    """
    from datetime import timezone
    local_dt = naive_dt.replace(tzinfo=get_user_tz())
    return local_dt.astimezone(timezone.utc)


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Settings (JSON) ──────────────────────────────────────────────────────────

def load_settings() -> dict:
    _ensure_dir()
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            return {**_DEFAULTS, **stored}
        except (json.JSONDecodeError, IOError):
            pass
    return dict(_DEFAULTS)


def save_settings(patch: dict) -> dict:
    """Merge *patch* into current settings and persist."""
    _ensure_dir()
    current = load_settings()
    current.update(patch)
    with _lock:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    return current


# ── Soul (plain text) ────────────────────────────────────────────────────────

def load_soul() -> str:
    _ensure_dir()
    if _SOUL_FILE.exists():
        return _SOUL_FILE.read_text(encoding="utf-8")
    return ""


def save_soul(text: str) -> None:
    _ensure_dir()
    _SOUL_FILE.write_text(text, encoding="utf-8")
