"""REST API for server-side settings and soul prompt.

All endpoints require Bearer authentication except /ping and /local-token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from infrastructure.auth import AUTH_TOKEN, require_auth
from infrastructure.settings_store import (
    load_settings,
    load_soul,
    save_settings,
    save_soul,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class SettingsPatch(BaseModel):
    ai_name: str | None = None
    openrouter_api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    history_pairs: int | None = None
    memory_cutoff_days: int | None = None
    pushy_api_key: str | None = None
    pushy_device_token: str | None = None
    reflection_cooldown_hours: int | None = None
    reflection_interval_hours: int | None = None


class SoulBody(BaseModel):
    text: str


# ── Settings CRUD ────────────────────────────────────────────────────────────

@router.get("")
async def get_settings(_token: str = Depends(require_auth)):
    data = load_settings()
    masked = {**data}
    for field in ("openrouter_api_key", "pushy_api_key"):
        val = masked.get(field, "")
        if val and len(val) > 8:
            masked[field] = val[:4] + "…" + val[-4:]
    return masked


@router.get("/raw")
async def get_settings_raw(_token: str = Depends(require_auth)):
    """Return settings with full (unmasked) API key — for local client only."""
    return load_settings()


@router.put("")
async def put_settings(body: SettingsPatch, _token: str = Depends(require_auth)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = save_settings(patch)
    return {"ok": True, "settings": updated}


# ── Soul CRUD ────────────────────────────────────────────────────────────────

@router.get("/soul")
async def get_soul(_token: str = Depends(require_auth)):
    return {"text": load_soul()}


@router.put("/soul")
async def put_soul(body: SoulBody, _token: str = Depends(require_auth)):
    save_soul(body.text)
    return {"ok": True}


# ── Reflection trigger ────────────────────────────────────────────────────────

@router.put("/trigger-reflection")
async def trigger_reflection(_token: str = Depends(require_auth)):
    """Manually kick off a reflection cycle (for testing)."""
    import asyncio
    try:
        from infrastructure.autonomy.reflection_engine import run as _reflect
        from infrastructure.settings_store import load_settings
        api_key = load_settings().get("openrouter_api_key", "")
        if not api_key:
            return {"ok": False, "error": "no_api_key"}
        asyncio.create_task(_reflect("default", api_key))
        return {"ok": True, "message": "reflection started"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Public endpoints (no auth) ────────────────────────────────────────────────

@router.get("/ping", dependencies=[])
async def ping():
    return {"status": "ok"}


@router.post("/verify-token")
async def verify_token(_token: str = Depends(require_auth)):
    """Client sends token, gets 200 if valid, 401 if not."""
    return {"ok": True}


@router.get("/local-token", dependencies=[])
async def local_token(request: Request):
    """Return auth token ONLY to localhost clients (127.0.0.1 / ::1).

    This lets the Electron desktop app auto-configure itself without
    the user having to copy-paste the token from the console.
    Remote clients get 403.
    """
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return {"token": AUTH_TOKEN}
    return {"error": "forbidden"}
