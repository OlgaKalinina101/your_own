"""File-based settings store.

Settings live in ``data/settings.json``, soul prompt in ``data/soul.md``.
Both are read/written by the REST API and consumed by the chat endpoint
so that clients never need to send secrets with every request.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SETTINGS_FILE = _DATA_DIR / "settings.json"
_SOUL_FILE = _DATA_DIR / "soul.md"

_DEFAULTS: dict[str, object] = {
    "openrouter_api_key": "",
    "model": "anthropic/claude-opus-4.6",
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
}

_lock = Lock()


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
