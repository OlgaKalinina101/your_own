"""Settings the person using this decides while it runs.

Stored in ``data/settings.json``, soul prompt in ``data/soul.md``. Both are
read and written through the REST API and consumed by the chat endpoint, so a
client never has to send secrets with every request. A change here is in force
on the next request; nothing needs restarting.

The other half is :mod:`settings` — deployment-time configuration from
``.env``: where the database is, which embedding model, and the bounds this
module's values are clamped to. Its docstring explains the split, including why
``history_pairs`` and ``memory_cutoff_days`` legitimately appear in both.

``user_timezone`` lives here, but everything that *uses* it lives in
:mod:`infrastructure.clock` — this module stores the setting, the clock owns
what it means.
"""
from __future__ import annotations

import json
import logging
from threading import Lock

from infrastructure.paths import DATA_DIR
from infrastructure.state_file import atomic_write_text, read_json

logger = logging.getLogger("settings_store")

_DATA_DIR = DATA_DIR
_SETTINGS_FILE = _DATA_DIR / "settings.json"
_SOUL_FILE = _DATA_DIR / "soul.md"

DEFAULT_MODEL = "~anthropic/claude-fable-latest"

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
    "body_image_model": "sourceful/riverflow-v2.5-fast",
    # ResearchAgent — the single orchestrator behind every search
    "research_model": "~google/gemini-pro-latest",
    "research_web_engine": "parallel",   # parallel (cheap) | exa (richer)
    "research_max_attempts": 3,
}

_lock = Lock()


def _ensure_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Settings (JSON) ──────────────────────────────────────────────────────────

def load_settings() -> dict:
    """Return stored settings merged over the defaults.

    A file that does not parse is loud and is set aside, not swallowed. It used
    to fall through to ``_DEFAULTS`` in silence, which read as "no OpenRouter
    key configured": the chat got 401s, the reflection worker logged
    ``no api_key, skipping`` and went quiet for good, and the next save from the
    UI wrote the empty key back over the file. Losing a key is survivable;
    losing it without a word is not.
    """
    _ensure_dir()
    stored = read_json(_SETTINGS_FILE, default={}, log=logger)
    return {**_DEFAULTS, **stored} if stored else dict(_DEFAULTS)


def save_settings(patch: dict) -> dict:
    """Merge *patch* into current settings and persist."""
    _ensure_dir()
    with _lock:
        # Read inside the lock: this is a read-modify-write, and two callers
        # interleaving here lose one of the patches.
        current = load_settings()
        current.update(patch)
        atomic_write_text(
            _SETTINGS_FILE, json.dumps(current, indent=2, ensure_ascii=False)
        )
    return current


# ── Soul (plain text) ────────────────────────────────────────────────────────

def load_soul() -> str:
    _ensure_dir()
    if _SOUL_FILE.exists():
        return _SOUL_FILE.read_text(encoding="utf-8")
    return ""


def save_soul(text: str) -> None:
    _ensure_dir()
    atomic_write_text(_SOUL_FILE, text)
