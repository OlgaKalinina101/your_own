"""REST API for server-side settings and soul prompt.

All endpoints require Bearer authentication except /ping.
"""
from __future__ import annotations

from infrastructure.account import ACCOUNT_ID
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from infrastructure.auth import require_auth
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
    enabled_skills: list[str] | None = None
    body_image_model: str | None = None
    research_model: str | None = None
    research_web_engine: str | None = None
    research_max_attempts: int | None = None


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


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/skills")
async def get_skills(_token: str = Depends(require_auth)):
    """Return all registered skills with their enabled status."""
    from infrastructure.skills.registry import get_all

    settings = load_settings()
    enabled_ids = settings.get("enabled_skills")

    skills_out = []
    for s in get_all():
        skills_out.append({
            "id": s.id,
            "cmd_name": s.cmd_name,
            "display": s.display,
            "description": s.description,
            "example": s.example,
            "action_type": s.action_type,
            "enabled": enabled_ids is None or s.id in enabled_ids,
        })
    return {"skills": skills_out}


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
        task = asyncio.create_task(_reflect(ACCOUNT_ID, api_key))
        task.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
        return {"ok": True, "message": "reflection started"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Workbench latest entry ───────────────────────────────────────────────────

@router.get("/workbench/latest")
async def workbench_latest(
    account_id: str = ACCOUNT_ID,
    _token: str = Depends(require_auth),
):
    """Return the most recent workbench note for the given account."""
    from infrastructure.autonomy.workbench import read as wb_read, parse_entries
    content = wb_read(account_id)
    entries = parse_entries(content) if content else []
    if not entries:
        return {"ts": None, "text": None}
    ts, text = entries[-1]
    import re
    # Strip markdown syntax chars
    clean = re.sub(r"[#*_`>\[\]]+", "", text)
    # Replace paragraph breaks with a bullet separator, single newlines with space
    clean = re.sub(r"\n{2,}", "  ·  ", clean)
    clean = clean.replace("\n", " ")
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return {"ts": ts, "text": clean}


# ── Workbench paginated entries ───────────────────────────────────────────────

@router.get("/workbench/entries")
async def workbench_entries(
    account_id: str = ACCOUNT_ID,
    offset: int = 0,
    limit: int = 25,
    _token: str = Depends(require_auth),
):
    """Paginated workbench entries (file + Chroma archive), newest first."""
    import logging
    from infrastructure.autonomy.workbench import read as wb_read, parse_entries
    from infrastructure.memory.chroma_pipeline import _get_archive_collection

    content = wb_read(account_id)
    file_entries = list(reversed(parse_entries(content))) if content else []
    file_count = len(file_entries)

    entries_out: list[dict] = []
    remaining = limit

    if offset < file_count:
        chunk = file_entries[offset : offset + limit]
        entries_out.extend({"ts": ts, "text": text} for ts, text in chunk)
        remaining -= len(chunk)

    if remaining > 0:
        archive_offset = max(0, offset - file_count)
        col = _get_archive_collection()
        if col is not None:
            try:
                result = col.get(
                    where={"account_id": account_id},
                    include=["documents", "metadatas"],
                )
                ids = result.get("ids") or []
                docs = result.get("documents") or []
                metas = result.get("metadatas") or []

                archive_rows = sorted(
                    zip(ids, docs, metas),
                    key=lambda r: r[2].get("created_at", ""),
                    reverse=True,
                )
                for _, doc, meta in archive_rows[archive_offset : archive_offset + remaining]:
                    entries_out.append({
                        "ts": meta.get("created_at", ""),
                        "text": doc,
                    })
            except Exception as exc:
                logging.getLogger(__name__).warning("[workbench/entries] archive query failed: %s", exc)

    total_archive = 0
    col = _get_archive_collection()
    if col is not None:
        try:
            total_archive = col.count()
        except Exception:
            pass

    has_more = (offset + limit) < (file_count + total_archive)
    return {"entries": entries_out, "has_more": has_more}


# ── Identity ─────────────────────────────────────────────────────────────────

@router.get("/identity")
async def get_identity(
    account_id: str = ACCOUNT_ID,
    _token: str = Depends(require_auth),
):
    """Return raw identity.md content."""
    from infrastructure.autonomy import identity_memory
    text = identity_memory.read(account_id)
    return {"text": text or ""}


# ── Public endpoints (no auth) ────────────────────────────────────────────────

@router.get("/ping", dependencies=[])
async def ping():
    return {"status": "ok"}


@router.get("/media-signature")
async def media_signature(_token: str = Depends(require_auth)):
    """Short-lived proof of auth for `<img src=…>`, which cannot send a header.

    The client appends the value as `?sig=` to media URLs. See the block above
    :func:`infrastructure.auth.issue_media_signature` for why the master token
    itself must not go there.
    """
    from infrastructure.auth import issue_media_signature

    signature, ttl = issue_media_signature()
    return {"sig": signature, "expires_in": ttl}


@router.post("/rotate-token")
async def rotate_token_endpoint(_token: str = Depends(require_auth)):
    """Issue a new token; the old one stops working at once.

    Requires the current token, so only someone who already has it can do this.
    Every other client — the phone, another browser — is signed out and needs
    the new value, which is the correct outcome after a leak.
    """
    from infrastructure.auth import rotate_token

    return {"ok": True, "token": rotate_token()}


@router.post("/verify-token")
async def verify_token(_token: str = Depends(require_auth)):
    """Client sends token, gets 200 if valid, 401 if not."""
    return {"ok": True}


# GET /local-token is deliberately gone.
#
# It returned AUTH_TOKEN, without auth, to any caller whose socket address was
# 127.0.0.1 — and a reverse proxy makes every remote caller look like that.
# This app ships one: next.config.mjs rewrites /api/:path* to the backend so
# the UI works through an ngrok tunnel. Measured end to end: a request from
# 192.168.31.37 straight to the backend got {"error":"forbidden"}, the same
# request through the rewrite got the real token. Anyone with the tunnel URL
# had the key to the whole API.
#
# The desktop app now reads data/auth_token.txt off disk over IPC
# (get-backend-auth-token in electron/main.js) — it starts the backend and
# knows where the file is. Browsers paste the token in Settings.
