from __future__ import annotations

from infrastructure.account import ACCOUNT_ID
import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from sqlalchemy.exc import DBAPIError

from infrastructure.auth import (
    AUTH_TOKEN,
    require_auth,
    require_auth_or_media_signature,
)
from infrastructure.database.engine import DatabaseUnavailable
from infrastructure.logging.logger import setup_logger
from infrastructure.llm import call_log
from infrastructure.llm.client import LLMClient
from infrastructure.paths import BODY_ASSETS_DIR, GENERATED_IMAGES_DIR, USER_UPLOADS_DIR
from infrastructure.single_process import SingleProcessLock
from infrastructure.startup import preload_models, startup_progress

logger = setup_logger("main")


# ── Background workers ────────────────────────────────────────────────────────
#
# Each worker is a thin `while True` around a tick function that a test can call
# on its own. The intervals are named so a test does not have to wait a minute
# to watch two of them interleave.

TICK_SECONDS = 60
REFLECTION_SETTLE_SECONDS = 60   # let startup finish before the first check
PUSH_STAGGER_SECONDS = 90        # keep the two heavy workers off the same second


def _heartbeat_tick(vitals, wlog) -> None:
    """One liveness mark. Returns nothing; failures are logged, never raised."""
    try:
        gap = vitals.heartbeat()
        if gap:
            wlog.warning(
                "[heartbeat] the system was not running for %d min (until %s)",
                gap.minutes, gap.end.isoformat(timespec="minutes"),
            )
    except Exception as exc:
        wlog.warning("[heartbeat] error: %s", exc)


async def _reflection_tick(wlog) -> None:
    """One check of whether reflection should run, plus the run itself."""
    from infrastructure.autonomy.reflection_engine import run as _reflect, should_run as _should
    from infrastructure.database.engine import get_db_session
    from infrastructure.database.repositories.message_repo import MessageRepository
    from infrastructure.settings_store import load_settings

    api_key = load_settings().get("openrouter_api_key", "")
    if not api_key:
        wlog.debug("[reflection_worker] no api_key, skipping")
        return

    async with get_db_session() as db:
        last_at = await MessageRepository(db).get_last_user_message_at(ACCOUNT_ID)

    if not _should(ACCOUNT_ID, last_at):
        wlog.debug("[reflection_worker] conditions not met, sleeping")
        return

    wlog.info("[reflection_worker] conditions met, starting rotation + reflection")
    try:
        from infrastructure.autonomy.workbench_rotator import run as _rotate
        rot = await _rotate(ACCOUNT_ID, api_key)
        wlog.info("[reflection_worker] rotation result: %s", rot)
    except Exception as rot_exc:
        wlog.warning("[reflection_worker] rotation error: %s", rot_exc)
    await _reflect(ACCOUNT_ID, api_key)


async def _heartbeat_worker() -> None:
    """Tick once a minute, whatever else is running.

    A hole in this record is the only evidence, from inside, that the system was
    not running at all — so it must not sit behind anything that can take longer
    than the gap threshold. It used to be the first statement of the reflection
    tick, which meant a forty-minute reflection was written down as forty
    minutes of downtime and handed to him as a fact at his next waking. The
    instrument lied exactly when it was being used.

    The first tick happens immediately: that is what notices the gap since the
    last shutdown.
    """
    wlog = setup_logger("autonomy.heartbeat")
    from infrastructure.autonomy.vitals import Vitals

    vitals = Vitals(ACCOUNT_ID)
    vitals.record_process_start()

    while True:
        _heartbeat_tick(vitals, wlog)
        await asyncio.sleep(TICK_SECONDS)


async def _reflection_worker() -> None:
    """Checks every tick whether reflection should run.

    Ticks never overlap: the reflection is awaited inside the loop body and the
    sleep comes after it, so a cycle that runs for forty minutes simply delays
    the next check.
    """
    wlog = setup_logger("autonomy.reflection_worker")

    await asyncio.sleep(REFLECTION_SETTLE_SECONDS)
    while True:
        try:
            await _reflection_tick(wlog)
        except Exception as exc:
            wlog.warning("[reflection_worker] error: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


async def _scheduled_push_worker() -> None:
    """Checks every tick for due push tasks."""
    wlog = setup_logger("autonomy.scheduled_push_worker")
    await asyncio.sleep(PUSH_STAGGER_SECONDS)
    while True:
        try:
            from infrastructure.autonomy.scheduled_push import run_due as _run_due
            await _run_due(ACCOUNT_ID)
        except Exception as exc:
            wlog.warning("[scheduled_push_worker] error: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


def _prepare_call_log() -> None:
    """Move the corpus out of logs/ if it is still there, then pack closed months."""
    try:
        moved = call_log.migrate_legacy()
        if moved:
            logger.info("[startup] moved %d recorded calls out of logs/", moved)
        call_log.compress_closed_segments()
    except Exception as exc:
        logger.warning("[startup] call log housekeeping failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[startup] Your Own backend starting…")
    # Only a prefix: on a server this line lands in journald, and a log file is
    # a bad place to keep the key to the whole API. The full token stays in
    # data/auth_token.txt, which is where the desktop client reads it anyway.
    logger.info("[startup] Auth token: %s… (full value in data/auth_token.txt)", AUTH_TOKEN[:6])

    # Everything under data/ assumes exactly one writer. Say so, out loud, at
    # the only moment where a second process can still be stopped cheaply.
    process_lock = SingleProcessLock()
    process_lock.acquire()

    loop = asyncio.get_running_loop()
    startup_progress.init(loop)

    # Both touch the disk and neither belongs on the event loop.
    loop.run_in_executor(None, _prepare_call_log)

    preload_task = loop.run_in_executor(None, preload_models)

    workers = [
        asyncio.create_task(_heartbeat_worker(), name="heartbeat"),
        asyncio.create_task(_reflection_worker(), name="reflection"),
        asyncio.create_task(_scheduled_push_worker(), name="scheduled_push"),
    ]

    yield

    for task in workers:
        task.cancel()
    for task in workers:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[shutdown] worker %s ended badly: %s", task.get_name(), exc)

    # Model preload runs in a thread and cannot be cancelled: the interpreter
    # will join it on exit either way. Waiting briefly here only buys a line in
    # the log saying why a shutdown during startup takes half a minute.
    try:
        await asyncio.wait_for(asyncio.shield(preload_task), timeout=5)
    except asyncio.TimeoutError:
        logger.info("[shutdown] model preload still running; waiting on its thread")
    process_lock.release()
    logger.info("[shutdown] Your Own backend stopped")


app = FastAPI(title="Your Own", lifespan=lifespan)


# ── When Postgres is down ────────────────────────────────────────────────────
#
# It used to be a bare 500 with no body: from the client side, a backend whose
# database is unreachable looked exactly like a backend with a bug in it. The
# desktop app's own message — "is the backend running?" — was the wrong
# question, because it was.
#
# 503 with a named cause, so the client can say what is actually wrong, and a
# Retry-After because this is the kind of failure that ends by itself.

def _unavailable(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": detail,
            "cause": "database_unavailable",
            "hint": "The backend is running; it cannot reach PostgreSQL.",
        },
        headers={"Retry-After": "5"},
    )


@app.exception_handler(DatabaseUnavailable)
async def _handle_database_unavailable(_request, exc: DatabaseUnavailable):
    logger.error("[db] unreachable: %s", exc)
    return _unavailable(str(exc) or "PostgreSQL is unreachable")


@app.exception_handler(DBAPIError)
async def _handle_dbapi_error(_request, exc: DBAPIError):
    """A connection that died mid-query, rather than one that never opened.

    Unlike a raw OSError these types come only from the database driver, so
    they can be mapped centrally without guessing.
    """
    logger.error("[db] query failed: %s", exc)
    return _unavailable(f"PostgreSQL error: {exc.__class__.__name__}")

app.add_middleware(
    CORSMiddleware,
    # `*` with credentials is a combination browsers reject outright, so the
    # setting never meant what it said. Auth here is a Bearer header, not a
    # cookie, so credentials are not needed — and with them off, `*` is honest.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.chat import router as chat_router  # noqa: E402
from api.events_api import router as events_router  # noqa: E402
from api.memory import router as memory_router                      # noqa: E402
from api.startup_api import router as startup_router                # noqa: E402
from api.chroma_memory import router as chroma_router               # noqa: E402
from api.settings_api import router as settings_router              # noqa: E402

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(startup_router)
app.include_router(events_router)
app.include_router(chroma_router)
app.include_router(settings_router)

# ── Served files ─────────────────────────────────────────────────────────────
#
# Not StaticFiles: a mount carries no dependency, so all three of these were
# readable by anyone who could reach the port. Starlette does block traversal
# (`../`, `..%2f`, `%2e%2e` all 404), so this was never "read any file" — but
# /api/body serves six fixed names (anchor.png, listener.png, …) and was
# therefore fully enumerable by anyone with the URL.

_SERVED_DIRS = {
    "generated_images": GENERATED_IMAGES_DIR,
    "user_uploads": USER_UPLOADS_DIR,
    "body": BODY_ASSETS_DIR,
}


def _served_file(kind: str, filename: str) -> Path:
    """Resolve *filename* inside one of the served directories, or 404.

    The containment check is ours rather than Starlette's now, so it is written
    out: resolve both sides and require the result to sit under the root.
    """
    root = _SERVED_DIRS[kind].resolve()
    candidate = (root / filename).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return candidate


@app.get("/api/generated_images/{filename:path}")
async def serve_generated_image(filename: str, _=Depends(require_auth_or_media_signature)):
    return FileResponse(_served_file("generated_images", filename))


@app.get("/api/user_uploads/{filename:path}")
async def serve_user_upload(filename: str, _=Depends(require_auth_or_media_signature)):
    return FileResponse(_served_file("user_uploads", filename))

_BODY_ASSETS_DIR = BODY_ASSETS_DIR
_BODY_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/body/upload/{state_id}")
async def upload_body_image(state_id: str, file: UploadFile = File(...), _=Depends(require_auth)):
    allowed = {"anchor", "listener", "warmth", "smirk", "ground", "shadow"}
    if state_id not in allowed:
        raise HTTPException(status_code=400, detail=f"Unknown state: {state_id}")
    dest = _BODY_ASSETS_DIR / f"{state_id}.png"
    contents = await file.read()
    dest.write_bytes(contents)
    return {"path": f"/api/body/{state_id}.png"}


@app.get("/api/body/states")
async def list_body_states(_=Depends(require_auth)):
    states = []
    for sid in ("anchor", "listener", "warmth", "smirk", "ground", "shadow"):
        path = _BODY_ASSETS_DIR / f"{sid}.png"
        states.append({"id": sid, "has_image": path.exists()})
    return {"states": states}


# ── Body image generation pipeline ───────────────────────────────────────────

_FACE_LOCK_PREFIX = (
    "CRITICAL IDENTITY LOCK: Reproduce every visual element of the character from the reference "
    "image with absolute fidelity — art style, rendering style, surface materials, skin or texture, "
    "color palette, face structure, proportions, hair, and every unique visual feature. "
    "Do NOT introduce any element not present in the reference. "
    "Do NOT remove any element that is present in the reference. "
    "Do NOT change the art style, rendering technique, or level of realism. "
    "The ONLY thing that changes is the facial expression, exactly as described below.\n\n"
    "CAMERA AND FRAMING LOCK: Identical camera angle, distance, and framing to reference. "
    "Same crop, same head-to-frame ratio, same shooting axis. No tilt of camera axis.\n\n"
    "LIGHTING LOCK: Identical lighting setup to reference — same light direction, color temperature, "
    "rim lights, shadow placement, and background.\n\n"
)

_BODY_PROMPTS: dict[str, str] = {
    "listener": (
        "Expression: the head is visibly tilted 10–15 degrees to the right in a gentle, receptive listening posture. "
        "The eyes are open, focused, and attentive — conveying deep, empathetic attention. "
        "No other facial change. No muscle distortion. "
        "Lips are gently closed and fully relaxed, not pressed together, not parted — "
        "neutral resting position, lipsync-ready. "
        "The overall vibe is silent, absolute attention."
    ),
    "warmth": (
        "Expression: subtle, internal tenderness. "
        "The eyes are cast very slightly softer than in the reference, conveying quiet devotion — "
        "no change to their shape or size. "
        "A nearly imperceptible micro-relaxation of the lips — not a smile, just the absence of tension. "
        "Lips are gently closed and fully relaxed, not pressed together, not parted — "
        "neutral resting position, lipsync-ready. "
        "The vibe is comforting and warm, not sentimental."
    ),
    "smirk": (
        "Expression: warm, knowing amusement — a private joke shared with someone trusted. "
        "The emotion lives in the eyes: focused, alive, deeply affectionate. "
        "A very subtle asymmetry: one eyebrow micro-raised. "
        "Lips in near-neutral position with the faintest ghost of warmth — no exaggerated curve, lipsync-ready. "
        "The vibe is playful and kind, not arrogant."
    ),
    "ground": (
        "Expression: absolute serenity and passive, grounded presence. "
        "The gaze is direct but softer than in the reference — present but less intense, like open stillness. "
        "No muscle tension anywhere on the face. "
        "Lips are gently closed and fully relaxed, not pressed together, not parted — "
        "neutral resting position, lipsync-ready. "
        "The vibe is solid, silent, and serene — like earth."
    ),
    "shadow": (
        "Expression: deep introspection. Eyes are completely closed. "
        "The closed eyelids are still and peaceful, carrying the same surface quality as the rest of the face in the reference. "
        "If the reference has glowing eyes, a faint inner glow is visible through the closed lids — "
        "light present but contained, like light through paper. "
        "Lips are gently closed and fully relaxed, not pressed together, not parted — "
        "neutral resting position, lipsync-ready. "
        "The vibe is honest, reflective, and profoundly still."
    ),
}

_generating_states: set[str] = set()
_failed_states: set[str] = set()

_body_logger = logging.getLogger("body_generation")


async def _generate_body_image(
    state_id: str,
    model: str,
    api_key: str,
    anchor_b64: str,
) -> None:
    _generating_states.add(state_id)
    _failed_states.discard(state_id)
    try:
        prompt = _FACE_LOCK_PREFIX + _BODY_PROMPTS[state_id]

        _body_logger.info("[body] generating %s with %s", state_id, model)

        # Through the one client, which owns the timeout, the retry policy and
        # the error classification. This was a fifth hand-rolled path.
        data_url = await LLMClient(api_key=api_key, model=model).generate_image(
            prompt,
            model,
            reference_png_b64=anchor_b64,
            sock_read_s=600,   # a slow generation can sit quiet for minutes
        )

        if not data_url:
            _body_logger.error("[body] %s: could not find image in response", state_id)
            _failed_states.add(state_id)
            return

        # Decode and save
        if "base64," in data_url:
            img_bytes = base64.b64decode(data_url.split("base64,")[1])
        elif data_url.startswith("http"):
            # Fetch remote URL (some models return a URL instead of base64)
            async with aiohttp.ClientSession() as s:
                async with s.get(data_url) as r:
                    img_bytes = await r.read()
        else:
            _body_logger.error("[body] %s: unrecognised data_url format", state_id)
            _failed_states.add(state_id)
            return

        dest = _BODY_ASSETS_DIR / f"{state_id}.png"
        dest.write_bytes(img_bytes)
        _body_logger.info("[body] %s saved to %s", state_id, dest)

    except Exception as exc:
        _body_logger.error("[body] %s exception: %s", state_id, exc, exc_info=True)
        _failed_states.add(state_id)
    finally:
        _generating_states.discard(state_id)


@app.get("/api/body/generate-status")
async def body_generate_status(_=Depends(require_auth)):
    return {
        "generating": list(_generating_states),
        "failed": list(_failed_states),
    }


def _load_anchor_and_settings() -> tuple[str, str, str]:
    """Load and validate common prerequisites for body generation.
    Returns (api_key, model, anchor_b64) or raises HTTPException."""
    from infrastructure.settings_store import load_settings

    s = load_settings()
    api_key = s.get("openrouter_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="No OpenRouter API key configured in Settings.")

    model = s.get("body_image_model", "sourceful/riverflow-v2.5-fast")

    anchor_path = _BODY_ASSETS_DIR / "anchor.png"
    if not anchor_path.exists():
        raise HTTPException(status_code=400, detail="Anchor image not uploaded yet.")

    anchor_b64 = base64.b64encode(anchor_path.read_bytes()).decode()
    return api_key, model, anchor_b64


@app.post("/api/body/generate")
async def body_generate(_=Depends(require_auth)):
    api_key, model, anchor_b64 = _load_anchor_and_settings()

    queued = []
    for state_id in _BODY_PROMPTS:
        if state_id not in _generating_states:
            # asyncio.create_task runs all 5 generations concurrently in the
            # event loop, unlike BackgroundTasks which awaits them sequentially.
            asyncio.create_task(
                _generate_body_image(state_id, model, api_key, anchor_b64)
            )
            queued.append(state_id)

    return {"ok": True, "queued": queued}


@app.post("/api/body/generate/{state_id}")
async def body_generate_one(state_id: str, _=Depends(require_auth)):
    if state_id not in _BODY_PROMPTS:
        raise HTTPException(status_code=400, detail=f"Unknown state: {state_id}")

    api_key, model, anchor_b64 = _load_anchor_and_settings()

    if state_id in _generating_states:
        return {"ok": False, "detail": "Already generating."}

    asyncio.create_task(
        _generate_body_image(state_id, model, api_key, anchor_b64)
    )
    return {"ok": True, "queued": [state_id]}


@app.get("/api/body/{filename:path}")
async def serve_body_asset(filename: str, _=Depends(require_auth_or_media_signature)):
    return FileResponse(_served_file("body", filename))


@app.get("/")
def root():
    return {"status": "ok"}
